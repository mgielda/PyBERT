# General purpose utilities for PyBERT.
#
# Original author: David Banas <capn.freako@gmail.com>
# Original date:   September 27, 2014 (Copied from `pybert_cntrl.py'.)
#
# Copyright (c) 2014 David Banas; all rights reserved World wide.

from numpy        import sign, sin, pi, array, linspace, float, zeros, ones, repeat, where, diff, log10, sqrt, power, exp, cumsum
from numpy.random import normal
from numpy.fft    import fft
from scipy.signal import lfilter, iirfilter, invres, freqs, medfilt
#from scipy.stats  import norm
from dfe          import DFE
from cdr          import CDR
import time
from pylab import *
import numpy as np
import scipy.stats as ss

debug = False

def moving_average(a, n=3) :
    ret = np.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return np.insert(ret[n - 1:], 0, ret[n - 1] * ones(n - 1)) / n

def find_crossing_times(t, x, min_delay=0., rising_first=True, min_init_dev=0.1):
    """
    Finds the zero crossing times of the input signal.

    Inputs:

      - t          Vector of sample times. Intervals do NOT need to be uniform.

      - x          Sampled input vector.

      - min_delay  Minimum delay required, before allowing crossings.
                   (Helps avoid false crossings at beginning of signal.)
                   Optional. Default = 0.

      - rising_first When True, start with the first rising edge found.
                     Optional. Default = True.
                     When this option is True, the first rising edge crossing
                     is the first crossing returned. This is the desired
                     behavior for PyBERT, because we always initialize the
                     bit stream with [0, 1, 1], in order to provide a known
                     synchronization point for jitter analysis.

      - min_init_dev The minimum initial deviation from zero, which must
                     be detected, before searching for crossings.
                     Normalized to maximum input signal magnitude.

    Outputs:

      - xings      The crossing times.

    """

    assert len(t) == len(x), "len(t) (%d) and len(x) (%d) need to be the same." % (len(t), len(x))

    t = array(t)
    x = array(x)

    max_mag_x = max(abs(x))
    min_mag_x = min_init_dev * max_mag_x
    i = 0
    while(abs(x[i]) < min_mag_x):
        i += 1
        assert i < len(x), "ERROR: find_crossing_times(): Input signal minimum deviation not detected!"
    x = x[i:]
    t = t[i:]

    sign_x      = sign(x)
    sign_x      = where(sign_x, sign_x, ones(len(sign_x))) # "0"s can produce duplicate xings.
    diff_sign_x = diff(sign_x)
    xing_ix     = where(diff_sign_x)[0]
    xings       = [t[i] + (t[i + 1] - t[i]) * x[i] / (x[i] - x[i + 1]) for i in xing_ix]

    min_time = t[0]
    if(min_delay):
        assert min_delay < t[-1], "Error: min_delay must be less than final time value."
        i = 0
        while(i < len(t) and t[i] < min_delay):
            i += 1
        min_time = t[i]

    i = 0
    while(xings[i] < min_time):
        i += 1

    if(rising_first and diff_sign_x[i] < 0.):
        i += 1

    if(debug):
        print "find_crossing_times(): min_delay:", min_delay, "; first crossing returned:", xings[i]

    return array(xings[i:])

def calc_jitter(ui, nbits, pattern_len, ideal_xings, actual_xings, rel_thresh=4, num_bins=99):
    """
    Calculate the jitter in a set of actual zero crossings, given the ideal crossings and unit interval.

    Inputs:

      - ui               : The nominal unit interval.

      - nbits            : The number of unit intervals spanned by the input signal.

      - pattern_len      : The number of unit intervals, before input bit stream repeats.

      - ideal_xings      : The ideal zero crossing locations of the edges.

      - actual_xings     : The actual zero crossing locations of the edges.

      - rel_thresh       : (optional) The threshold for determining periodic jitter spectral components (sigma).

      - num_bins         : (optional) The number of bins to use, when forming histograms.

    Outputs:

      - jitter   : The total jitter.

      - t_jitter : The times (taken from 'ideal_xings') corresponding to the returned jitter values.

      - isi      : The peak to peak jitter due to intersymbol interference.

      - dcd      : The peak to peak jitter due to duty cycle distortion.

      - pj       : The peak to peak jitter due to uncorrelated periodic sources.

      - rj       : The standard deviation of the jitter due to uncorrelated unbounded random sources.

      - tie_ind  : The data independent jitter.

      - thresh   : Threshold for determining periodic components.

      - jitter_spectrum  : The spectral magnitude of the total jitter.

      - tie_ind_spectrum : The spectral magnitude of the data independent jitter.

      - spectrum_freqs   : The frequencies corresponding to the spectrum components.

      - hist        : The histogram of the actual jitter.

      - hist_synth  : The histogram of the extrapolated jitter.

      - bin_centers : The bin center values for both histograms.

    """

    # Check alignment between actual and ideal crossings.
    if(len(ideal_xings) > len(actual_xings)):
        if(max(abs(diff(actual_xings) - diff(ideal_xings[:len(actual_xings)]))) > ui):
            print "INFO: calc_jitter(): Possibly missing actual crossings."
    else:
        if(max(abs(diff(actual_xings[:len(ideal_xings)]) - diff(ideal_xings))) > ui):
            print "WARNING: calc_jitter(): Potential misalignment between actual and ideal crossings!"

    jitter   = []
    t_jitter = []
    i        = 0
#    zero_pads = 0
    pads     = 0
    # Assemble the TIE track.
    ideal_xings  = array(ideal_xings)  - (ideal_xings[0] - ui / 2.)
    actual_xings = array(actual_xings) - (actual_xings[0] - ideal_xings[0])

    for ideal_xing in ideal_xings:
        # Find the first actual crossing occuring within [-ui/2, ui/2]
        # of the ideal crossing, checking for missing crossings.
        min_t = ideal_xing - 1.2 * ui / 2.
        max_t = ideal_xing + 1.2 * ui / 2.
        while(i < len(actual_xings) and actual_xings[i] < min_t):
            i += 1
        if(i == len(actual_xings)):  # We've exhausted the list of actual crossings; we're done.
            break
        if(actual_xings[i] > max_t): # Means the xing we're looking for didn't occur, in the actual signal.
            jitter.append(ui / 2 * (-1) ** pads) # Pad the jitter w/ alternating +/- UI/2.
            pads += 1
#            jitter.append(0.)        # Zero pad jitter vector for missing crossings.
#            zero_pads += 1
        else:
            tie = actual_xings[i] - ideal_xing
            if(tie < -ui / 2.):
                tie = -ui / 2.
            elif(tie > ui / 2.):
                tie = ui / 2.
            jitter.append(tie)
            i += 1
        t_jitter.append(ideal_xing)
    jitter  = array(jitter)

    if(debug):
        print "mean(jitter):", mean(jitter)
        print "len(jitter):", len(jitter)

    #jitter -= mean(jitter)

    # Separate the rising and falling edges, shaped appropriately for averaging over the pattern period.
    try:
#        xings_per_pattern = where(ideal_xings > pattern_len * ui + ui / 2.)[0][0]
        xings_per_pattern = where(ideal_xings >= pattern_len * ui)[0][0]
    except:
        print "ideal_xings:", ideal_xings
        raise

    assert(not (xings_per_pattern % 2)), "Odd number of crossings per pattern detected! ideal_xings[0]: %e; actual_xings[0]: %e." % (ideal_xings[0], actual_xings[0])
#    if(xings_per_pattern % 2):
#        print "Odd number of crossings per pattern detected!"
#        print "ideal_xings[0]:", ideal_xings[0], "actual_xings[0]:", actual_xings[0]

    risings_per_pattern  = fallings_per_pattern = xings_per_pattern // 2
    num_patterns         = nbits // pattern_len
#    num_patterns         = nbits // pattern_len - 1
#    jitter = jitter[xings_per_pattern:] # The first pattern period is problematic.
    if(len(jitter) < xings_per_pattern * num_patterns):
        jitter = np.append(jitter, zeros(xings_per_pattern * num_patterns - len(jitter)))
        print "Added %d zeros to 'jitter'." % (xings_per_pattern * num_patterns - len(jitter))

    try:
        t_jitter = t_jitter[:len(jitter)]
        if(len(jitter) > len(t_jitter)):
            jitter = jitter[:len(t_jitter)]
            print "Had to shorten 'jitter', due to 't_jitter'."
    except:
        print "jitter:", jitter
        raise

    x, valid_ix     = make_uniform(t_jitter, jitter, ui, nbits)

    y               = fft(x)
    jitter_spectrum = abs(y[:len(y) / 2]) / sqrt(len(jitter)) # Normalized, in order to make power correct.
    f0              = 1. / (ui * nbits)
    spectrum_freqs  = [i * f0 for i in range(len(y) / 2)]

    try:
        tie_risings          = reshape(jitter.take(range(0, num_patterns * risings_per_pattern * 2, 2)),  (num_patterns, risings_per_pattern))
        tie_fallings         = reshape(jitter.take(range(1, num_patterns * fallings_per_pattern * 2, 2)), (num_patterns, fallings_per_pattern))
    except:
        print "ideal_xings[xings_per_pattern - 1]:", ideal_xings[xings_per_pattern - 1], "ideal_xings[-1]:", ideal_xings[-1]
        print "num_patterns:", num_patterns, "risings_per_pattern:", risings_per_pattern, "fallings_per_pattern:", fallings_per_pattern, "len(jitter):", len(jitter)
        print "nbits:", nbits, "pattern_len:", pattern_len
        raise

    assert len(filter(lambda x: x == None, tie_risings)) == 0, "num_patterns: %d, risings_per_pattern: %d, len(jitter): %d" % \
                                           (num_patterns, risings_per_pattern, len(jitter))
    assert len(filter(lambda x: x == None, tie_fallings)) == 0, "num_patterns: %d, fallings_per_pattern: %d, len(jitter): %d" % \
                                           (num_patterns, fallings_per_pattern, len(jitter))
    # Do the jitter decomposition.
    # - Use averaging to remove the uncorrelated components, before calculating data dependent components.
    tie_risings_ave  = tie_risings.mean(axis=0)
    tie_fallings_ave = tie_fallings.mean(axis=0)
    try:
        isi = max(tie_risings_ave.ptp(), tie_fallings_ave.ptp())
    except:
        print "tie_risings_ave:", tie_risings_ave, "\ntie_fallings_ave:", tie_fallings_ave
        raise
    dcd = abs(mean(tie_risings_ave) - mean(tie_fallings_ave))
    # - Subtract the data dependent jitter from the original TIE track.
    tie_ave  = concatenate(zip(tie_risings_ave, tie_fallings_ave))
    tie_ave  = resize(tie_ave, len(jitter))
    try:
        tie_ind  = jitter - tie_ave
    except:
        print "tie_ave:", tie_ave
        raise

    # TEMPORARY DEBUGGING
    #print "", jitter

    # - Use spectral analysis to help isolate the periodic components of the data independent jitter.
    tie_ind_uniform, valid_ix = make_uniform(t_jitter, tie_ind, ui, nbits)

    # -- Normalized, in order to make power correct, since we grab Rj from the freq. domain.
    # -- (I'm using the length of the vector before zero padding, because zero padding doesn't add energy.)
    y        = fft(tie_ind_uniform) / sqrt(len(tie_ind))
    y_mag    = abs(y)
    tie_ind_spectrum = y_mag[:len(y_mag) / 2]
    y_mean   = moving_average(y_mag, n = len(y_mag) / 10)
    y_var    = moving_average((y_mag - y_mean) ** 2, n = len(y_mag) / 10)
    y_sigma  = sqrt(y_var)

    thresh   = y_mean + rel_thresh * y_sigma
    y_per    = where(y_mag > thresh, y, zeros(len(y)))
    y_rnd    = abs(where(y_mag > thresh, zeros(len(y)), y))
    rj       = sqrt(mean((y_rnd - mean(y_rnd)) ** 2))
    tie_per  = real(ifft(y_per)).take(valid_ix) * sqrt(len(tie_ind))

    pj       = tie_per.ptp()

    # - Reassemble the jitter, excluding the Rj.
    jitter_synth         = tie_ave + tie_per

    # - Calculate the histogram of original, for comparison.
    hist, bin_edges      = histogram(jitter, num_bins, (-ui/2., ui/2.))
    bin_centers          = [mean([bin_edges[i], bin_edges[i + 1]]) for i in range(len(bin_edges) - 1)]
#    hist[where(abs(array(bin_centers)) < ui / num_bins / 2)[0]] -= zero_pads
    hist = array(map(float, hist)) / sum(hist) # Make it a PMF.

    # - Calculate the histogram of everything, except Rj.
    hist_synth, bin_edges = histogram(jitter_synth, num_bins, (-ui/2., ui/2.))
#    hist_synth[where(abs(array(bin_centers)) < ui / num_bins / 2)[0]] -= zero_pads
    hist_synth = array(map(float, hist_synth)) / sum(hist_synth) # Make it a PMF.

    # - Extrapolate the tails by convolving w/ complete Gaussian.
    rv         = ss.norm(loc = 0., scale = rj)
    rj_pdf     = rv.pdf(bin_centers)
    rj_pdf     = rj_pdf / sum(rj_pdf)
    hist_synth = convolve(hist_synth, rj_pdf, mode='same')

    if(debug):
        plot(jitter,                                               label="jitter",          color="b")
        plot(concatenate(zip(tie_risings_ave, tie_fallings_ave)),  label="rise/fall aves.", color="r")
        title("Original TIE track & Average over one pattern period")
        legend()
        show()
        plot(t_jitter, tie_ind)
        title("Data Independent Jitter")
        show()
        plot(y_mag)
        plot(thresh)
        title("Data Independed Jitter Spectral Magnitude and Pj Threshold")
        show()
        print "# of spectral peaks detected:", len(where(y_per)[0])
        plot(tie_per)
        title("Periodic Jitter")
        show()
        hist, bin_edges             = histogram(tie_ind, 99, (-ui/2., ui/2.))
        bin_centers                 = [mean([bin_edges[i], bin_edges[i + 1]]) for i in range(len(bin_edges) - 1)]
        plot(bin_centers, hist)
        title("Data Independent Jitter Distribution")
        show()

    return (jitter, t_jitter, isi, dcd, pj, rj, tie_ind,
            thresh[:len(thresh) / 2], jitter_spectrum, tie_ind_spectrum, spectrum_freqs,
            hist, hist_synth, bin_centers)

def make_uniform(t, jitter, ui, nbits):
    """
    Make the jitter vector uniformly sampled in time, by zero-filling where necessary.

    The trick, here, is creating a uniformly sampled input vector for the FFT operation,
    since the jitter samples are almost certainly not uniformly sampled.
    We do this by simply zero padding the missing samples.

    Inputs:

    - t      : The sample times for the 'jitter' vector.

    - jitter : The input jitter samples.

    - ui     : The nominal unit interval.

    - nbits  : The desired number of unit intervals, in the time domain.

    Output:

    - y      : The uniformly sampled, zero padded jitter vector.

    - y_ix   : The indices where y is valid (i.e. - not zero padded).

    """

    assert len(t) == len(jitter), "Length of t (%d) and jitter (%d) must be equal!" % (len(t), len(jitter))

    run_lengths    = map(int, diff(t) / ui + 0.5)
    valid_ix       = [0] + list(cumsum(run_lengths))
    valid_ix       = filter(lambda x: x < nbits, valid_ix)
    missing        = where(array(run_lengths) > 1)[0]
    num_insertions = 0
    jitter         = list(jitter) # Because we use 'insert'.

    for i in missing:
        for j in range(run_lengths[i] - 1):
            jitter.insert(i + 1 + num_insertions, 0.)
            num_insertions += 1

    if(len(jitter) < nbits):
        jitter.extend([0.] * (nbits - len(jitter)))
    if(len(jitter) > nbits):
        jitter = jitter[:nbits]

    return jitter, valid_ix

def extrapolate_histogram(hist, rj, ber=1.e-15):
    """
    Extrapolate the tails of a histogram by convolving it
    """

def calc_jitter_spectrum(t, jitter, ui, nbits):
    """
    Calculate the spectral magnitude estimate of the input jitter samples.

    The output is normalized, so as to produce a value of 'A / ui' at location 'fj',
    when the input jitter contains a component 'A * sin(2*pi*fj + phi)'.

    Inputs:

    - t      : The sample times for the 'jitter' vector.

    - jitter : The input jitter samples.

    - ui     : The nominal unit interval.

    - nbits  : The desired number of unit intervals, in the time domain.

    Output:

    - f      : The frequency ordinate for the spectral magnitude output vector.

    - y      : The spectral magnitude estimates vector.

    """

    half_n = nbits / 2
    f0     = 1. / (ui * nbits)
    f      = [i * f0 for i in range(half_n)]
    y      = fft(make_uniform(t, jitter, ui, nbits))
    y      = array(abs(y[:half_n])) / half_n / ui
    return (f, y)

def calc_gamma(R0, w0, Rdc, Z0, v0, Theta0, ws):
    """
    Calculates propagation constant from cross-sectional parameters.

    The formula's applied are taken from Howard Johnson's "Metallic Transmission Model"
    (See "High Speed Signal Propagation", Sec. 3.1.)

    Inputs:
      - R0          skin effect resistance (Ohms/m)
      - w0          cross-over freq.
      - Rdc         d.c. resistance (Ohms/m)
      - Z0          characteristic impedance in LC region (Ohms)
      - v0          propagation velocity (m/s)
      - Theta0      loss tangent
      - ws          frequency sample points vector

    Outputs:
      - gamma       frequency dependent propagation constant
      - Zc          frequency dependent characteristic impedance
    """

    w = array(ws).copy()

    # Guard against /0.
    if(w[0] == 0):
        w[0] = 1.e-12

    Rac   = R0 * sqrt(2 * 1j * w / w0)                        # AC resistance vector
    R     = sqrt(np.power(Rdc, 2) + np.power(Rac, 2))         # total resistance vector
    L0    = Z0 / v0                                           # "external" inductance per unit length (H/m)
    C0    = 1. / (Z0 * v0)                                    # nominal capacitance per unit length (F/m)
    C     = C0 * np.power((1j * w / w0), (-2. * Theta0 / pi)) # complex capacitance per unit length (F/m)
    gamma = sqrt((1j * w * L0 + R) * (1j * w * C))            # propagation constant (nepers/m)
    Zc    = sqrt((1j * w * L0 + R) / (1j * w * C))            # characteristic impedance (Ohms)

    return (gamma, Zc)

def calc_G(H, Rs, Cs, Zc, RL, Cp, CL, ws):
    """
    Calculates fully loaded transfer function of complete channel.

    Inputs:
      - H     unloaded transfer function of interconnect
      - Rs    source series resistance
      - Cs    source parallel (parasitic) capacitance
      - Zc    frequency dependent characteristic impedance of the interconnect
      - RL    load resistance (differential)
      - Cp    load parallel (parasitic) capacitance (single ended)
      - CL    load series (d.c. blocking) capacitance (single ended)
      - ws    frequency sample points vector

    Outputs:
      - G     frequency dependent transfer function of channel
    """

    w = array(ws).copy()

    # Guard against /0.
    if(w[0] == 0):
        w[0] = 1.e-12

    # Impedance looking back into the Tx output is a simple parallel RC network.
    Zs = Rs / (1. + 1j * w * Rs * Cs)
    # Rx load impedance is 2 series, a.c.-coupling capacitors, in series w/ parallel comb. of Rterm & parasitic cap.
    # (The two parasitic capacitances are in series.)
    ZL = 2. * 1. / (1j * w * CL) + RL / (1. + 1j * w * RL * Cp / 2)
    # Admittance into the interconnect is (Cs || Zc) / (Rs + (Cs || Zc)).
    Cs_par_Zc = Zc / (1. + 1j * w * Zc * Cs)
    A         = Cs_par_Zc / (Rs + Cs_par_Zc)
    # Reflection coefficient at Rx:
    R1        = (ZL - Zc) / (ZL + Zc)
    # Reflection coefficient at Tx:
    R2        = (Zs - Zc) / (Zs + Zc)
    # Fully loaded channel transfer function:
    G = A * H * (1 + R1) / (1 - R1 * R2 * H**2)
    G = G * (((RL/(1j*w*Cp/2))/(RL + 1/(1j*w*Cp/2))) / ZL) # Corrected for divider action.
                                                           # (i.e. - We're interested in what appears across RL.)
    return G

def calc_eye(ui, samps_per_bit, height, ys, clock_times=None):
    """
    Calculates the "eye" diagram of the input signal vector.

    Inputs:
      - ui             unit interval (s)
      - samps_per_bit  # of samples per bit
      - height         height of output image data array
      - ys             signal vector of interest
      - clock_times    (optional) vector of clock times to use for eye centers.
                       If not provided, just locate second zero-crossing (The first can be problematic.),
                       and assume constant UI and no phase jumps.
                       (This allows the same function to be used for eye diagram creation, for both pre and post-CDR signals.)

    Outputs:
      - img_array      The "heat map" representing the eye diagram.
                       Each grid location contains a value indicating
                       the number of times the signal passed through
                       that location.

    """

    # List/array necessities.
    ys = array(ys)

    # Intermediate variable calculation.
    tsamp = ui / samps_per_bit

    # Adjust the scaling.
    width    = 2 * samps_per_bit
    y_max    = 1.1 * max(abs(ys))
    y_scale  = height / (2 * y_max)          # (pixels/V)
    y_offset = height / 2                    # (pixels)

    # Generate the "heat" picture array.
    img_array = zeros([height, width])
    if(clock_times):
        for clock_time in clock_times:
            start_time = clock_time - ui
            stop_time  = clock_time + ui
            start_ix   = int(start_time / tsamp)
            interp_fac = (start_time - start_ix * tsamp) / tsamp
            last_y     = ys[start_ix]
            i = 0
            for (samp1, samp2) in zip(ys[start_ix : start_ix + 2 * samps_per_bit],
                                      ys[start_ix + 1 : start_ix + 1 + 2 * samps_per_bit]):
                y = samp1 + (samp2 - samp1) * interp_fac
                img_array[int(y * y_scale + 0.5) + y_offset, i] += 1
                if(sign(y) != sign(last_y)): # Trap zero crossings.
                    img_array[y_offset, int(i - 1 + y / (y - last_y) + 0.5)] += 1
                last_y = y
                i += 1
    else:
        start_ix      = (where(diff(sign(ys)))[0] % samps_per_bit).mean() + samps_per_bit // 2 
        last_start_ix = len(ys) - 2 * samps_per_bit
        while(start_ix < last_start_ix):
            last_y = ys[start_ix]
            i      = 0
            for y in ys[start_ix : start_ix + 2 * samps_per_bit]:
                img_array[int(y * y_scale + 0.5) + y_offset, i] += 1
                if(sign(y) != sign(last_y)): # Trap zero crossings.
                    img_array[y_offset, int(i - 1 + y / (y - last_y) + 0.5)] += 1
                last_y = y
                i += 1
            start_ix += samps_per_bit

    return img_array

def make_ctle(rx_bw, peak_freq, peak_mag, w):
    """
    Generate the frequency response of a continuous time linear
    equalizer (CTLE), given the:
     - signal path bandwidth,
     - peaking specification, and
     - list of frequencies of interest.

    We use the 'invres()' function from scipy.signal, as it suggests
    itself as a natural approach, given our chosen use model of having
    the user provide the peaking frequency and degree of peaking.

    That is, we define our desired frequency response using one zero
    and two poles, where:
    - The pole locations are equal to:
       - the signal path natural bandwidth, and
       - the user specified peaking frequency.
    - The zero location is chosen, so as to provide the desired degree
      of peaking.

    Inputs:

      - rx_bw        The natural (or, unequalized) signal path bandwidth (Hz).

      - peak_freq    The location of the desired peak in the frequency
                     response (Hz).

      - peak_mag     The desired relative magnitude of the peak (dB). (|H(0)| = 1)

      - w            The list of frequencies of interest (rads./s).

    Outputs:

      - w, H         The resultant complex frequency response, at the
                     given frequencies.

    """

    p2   = -2. * pi * rx_bw
    p1   = -2. * pi * peak_freq
    #z    = -p2 / peak_mag / 2.
    z    = p1 / pow(10., peak_mag / 20.)
    if(p2 != p1):
        r1   = (z - p1) / (p2 - p1)
        r2   = 1 - r1
    else:
        r1   = -1.
        r2   = z - p1
    b, a = invres([r1, r2], [p1, p2], [])

    return freqs(b, a, w)

def trim_impulse(g, Ts, chnl_dly=0.):
    """
    Trim impulse response, for more useful display, by:
      - eliminating 90% of the overall delay from the beginning, and
      - clipping off the tail, after 99.9% of the total power has been captured.

    Inputs:
    
      - g         impulse response

      - Ts        sample interval (same units as 'chnl_dly')

      - chnl_dly  (optional) channel delay

    Outputs:
    
      - g_trim    trimmed impulse response

      - start_ix  index of first returned sample

    """

    g         = array(g)
    start_ix  = int(0.9 * chnl_dly / Ts)
    Pt        = 0.999 * sum(g ** 2)
    i         = 0
    P         = 0
    while(P < Pt):
        P += g[i] ** 2
        i += 1

    return (g[start_ix : i], start_ix)

