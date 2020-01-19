#!/usr/bin/env python
'''Run a reblocking analysis on pauxy QMC output files.'''

import glob
import h5py
import json
import numpy
import pandas as pd
import pyblock
import scipy.stats
from pauxy.analysis.extraction import (
        extract_mixed_estimates,
        extract_data,
        get_metadata, set_info,
        extract_rdm, extract_mixed_rdm
        )
from pauxy.utils.misc import get_from_dict


def average_single(frame, delete=True):
    short = frame
    means = short.mean().to_frame().T
    err = short.aggregate(lambda x: scipy.stats.sem(x, ddof=1)).to_frame().T
    averaged = means.merge(err, left_index=True, right_index=True,
                           suffixes=('', '_error'))
    columns = [c for c in averaged.columns.values if '_error' not in c]
    columns = [[c, c+'_error'] for c in columns]
    columns = [item for sublist in columns for item in sublist]
    averaged.reset_index(inplace=True)
    delcol = ['ENumer', 'ENumer_error', 'EDenom',
              'EDenom_error', 'Weight', 'Weight_error']
    for d in delcol:
        if delete:
            columns.remove(d)
    return averaged[columns]


def average_ratio(numerator, denominator):
    re_num = numerator.real
    re_den = denominator.real
    im_num = numerator.imag
    im_den = denominator.imag
    # When doing FP we need to compute E = \bar{ENumer} / \bar{EDenom}
    # Only compute real part of the energy
    num_av = (re_num.mean()*re_den.mean()+im_num.mean()*im_den.mean())
    den_av = (re_den.mean()**2 + im_den.mean()**2)
    mean = num_av / den_av
    # Doing error analysis properly is complicated. This is not correct.
    re_nume = scipy.stats.sem(re_num)
    re_dene = scipy.stats.sem(re_den)
    # Ignoring the fact that the mean includes complex components.
    cov = numpy.cov(re_num, re_den)[0,1]
    nsmpl = len(re_num)
    error = abs(mean) * ((re_nume/re_num.mean())**2 +
                         (re_dene/re_den.mean())**2 -
                         2*cov/(nsmpl*re_num.mean()*re_den.mean()))**0.5

    return (mean, error)


def average_fp(frame):
    real = average_single(frame.apply(numpy.real), False)
    imag = average_single(frame.apply(numpy.imag), False)
    results = pd.DataFrame()
    re_num = real.ENumer
    re_den = real.EDenom
    im_num = imag.ENumer
    im_den = imag.EDenom
    # When doing FP we need to compute E = \bar{ENumer} / \bar{EDenom}
    # Only compute real part of the energy
    results['E'] = (re_num*re_den+im_num*im_den) / (re_den**2 + im_den**2)
    # Doing error analysis properly is complicated. This is not correct.
    re_nume = real.E_num_error
    re_dene = real.E_denom_error
    # Ignoring the fact that the mean includes complex components.
    cov = frame.apply(numpy.real).cov()
    cov_nd = cov['ENumer']['EDenom']
    nsmpl = len(frame)
    results['E_error'] = results.E * ((re_nume/re_num)**2 +
                                      (re_dene/re_den)**2 -
                                      2*cov_nd/(nsmpl*re_num*re_den))**0.5
    return results


def reblock_mixed(groupby, columns):
    analysed = []
    for group, frame in groupby:
        short = frame.reset_index().drop(columns+['index', 'Time', 'EDenom', 'ENumer', 'Weight'], axis=1)
        (data_len, blocked_data, covariance) = pyblock.pd_utils.reblock(short)
        print("data_len, blocked_data = {}, {}".format(data_len, blocked_data.shape))
        reblocked = pd.DataFrame()
        for c in short.columns:
            try:
                rb = pyblock.pd_utils.reblock_summary(blocked_data.loc[:,c])
                print(rb.to_string())
                reblocked[c] = rb['mean'].values
                reblocked[c+'_error'] = rb['standard error'].values
            except KeyError:
                print("Reblocking of {:4} failed. Insufficient "
                      "statistics.".format(c))
        for i, v in enumerate(group):
            reblocked[columns[i]] = v
        analysed.append(reblocked)


    return pd.concat(analysed)


def reblock_free_projection(groupby, columns):
    analysed = []
    for group, frame in groupby:
        short = frame[['ENumer', 'EDenom']].apply(numpy.real)
        (data_len, blocked_data, covariance) = pyblock.pd_utils.reblock(short)
        print("data_len, blocked_data = {}, {}".format(data_len, blocked_data.shape))
        reblocked = pd.DataFrame()
        denom = blocked_data.loc[:,'EDenom']
        for c in short.columns:
            if c != 'EDenom':
                nume = blocked_data.loc[:,c]
                cov = covariance.xs('EDenom', level=1)[c]
                ratio = pyblock.error.ratio(nume, denom, cov, data_len)
                rb = pyblock.pd_utils.reblock_summary(ratio)
                print(rb.to_string())
                try:
                    if c == 'ENumer':
                        c = 'ETotal'
                    reblocked[c] = rb['mean'].values
                    reblocked[c+'_error'] = rb['standard error'].values
                except KeyError:
                    print("Reblocking of {:4} failed. Insufficient "
                          "statistics.".format(c))
        for i, v in enumerate(group):
            reblocked[columns[i]] = v
        analysed.append(reblocked)

    if len(analysed) == 0:
        return None
    else:
        return pd.concat(analysed)


def reblock_local_energy(filename, skip=0):
    data = pauxy.analysis.extraction.extract_mixed_estimates(filename)
    results = reblock_mixed(data.apply(numpy.real)[skip:])
    if results is None:
        return None
    else:
        try:
            energy = results['ETotal'].values[0]
            error = results['ETotal_error'].values[0]
            return (energy, error)
        except KeyError:
            return None


def average_rdm(files, skip=1, est_type='back_propagated', rdm_type='one_rdm', ix=None):

    if (est_type == 'back_propagated'):
        rdm_series = extract_rdm(files, est_type=est_type, rdm_type=rdm_type, ix=ix)
    elif (est_type == 'basic'):
        rdm_series = extract_mixed_rdm(files, est_type=est_type, rdm_type=rdm_type)

    rdm_av = rdm_series[skip:].mean(axis=0)
    rdm_err = rdm_series[skip:].std(axis=0, ddof=1) / len(rdm_series)**0.5
    return rdm_av, rdm_err


def average_correlation(gf):
    ni = numpy.diagonal(gf, axis1=2, axis2=3)
    mg = gf.mean(axis=0)
    hole = 1.0 - numpy.sum(ni, axis=1)
    hole_err = hole.std(axis=0, ddof=1) / len(hole)**0.5
    spin = 0.5*(ni[:,0,:]-ni[:,1,:])
    spin_err = spin.std(axis=0, ddof=1) / len(hole)**0.5
    return (hole.mean(axis=0), hole_err, spin.mean(axis=0), spin_err, gf)


def average_tau(frames):

    data_len = frames.size()
    means = frames.mean()
    err = numpy.sqrt(frames.var())
    covs = frames.cov().loc[:,'ENumer'].loc[:, 'EDenom']
    energy = means['ENumer'] / means['EDenom']
    sqrtn = numpy.sqrt(data_len)
    energy_err = ((err['ENumer']/means['ENumer'])**2.0 +
                  (err['EDenom']/means['EDenom'])**2.0 -
                  2*covs/(means['ENumer']*means['EDenom']))**0.5

    energy_err = abs(energy/sqrtn) * energy_err
    # eproj = means['ETotal']
    # eproj_err = err['ETotal']/numpy.sqrt(data_len)
    # weight = means['Weight']
    # weight_error = err['Weight']
    # numerator = means['ENumer']
    # numerator_error = err['ENumer']
    results = pd.DataFrame({'ETotal': energy, 'ETotal_error': energy_err})

    return results


def analyse_back_propagation(frames):
    frames[['E', 'E1b', 'E2b']] = frames[['E','E1b','E2b']]
    frames = frames.apply(numpy.real)
    frames = frames.groupby(['nbp','dt'])
    data_len = frames.size()
    means = frames.mean().reset_index()
    # calculate standard error of the mean for grouped objects. ddof does
    # default to 1 for scipy but it's different elsewhere, so let's be careful.
    errs = frames.aggregate(lambda x: scipy.stats.sem(x, ddof=1)).reset_index()
    full = pd.merge(means, errs, on=['nbp','dt'], suffixes=('','_error'))
    columns = full.columns.values[2:]
    columns = numpy.insert(columns, 0, 'nbp')
    columns = numpy.insert(columns, 1, 'dt')
    return full[columns]


def analyse_itcf(itcf):
    means = itcf.mean(axis=(0,1), dtype=numpy.float64)
    n = itcf.shape[0]*itcf.shape[1]
    errs = (
        itcf.std(axis=(0,1), ddof=1, dtype=numpy.float64) / numpy.sqrt(n)
    )
    return (means, errs)


def analyse_simple(files, start_time):
    data = pauxy.analysis.extraction.extract_hdf5_data_sets(files)
    norm_data = []
    for (g, f) in zip(data, files):
        (m, norm, bp, itcf, itcfk, mixed_rdm, bp_rdm) = g
        dt = m.get('qmc').get('dt')
        free_projection = m.get('propagators').get('free_projection')
        step = m.get('qmc').get('nmeasure')
        read_rs = m.get('psi').get('read_file') is not None
        nzero = numpy.nonzero(norm['Weight'].values)[0][-1]
        start = int(start_time/(step*dt)) + 1
        if read_rs:
            start = 0
        if free_projection:
            reblocked = average_fp(norm[start:nzero])
        else:
            reblocked = reblock_mixed(norm[start:nzero].apply(numpy.real))
            columns = pauxy.analysis.extraction.set_info(reblocked, m)
        norm_data.append(reblocked)
    return pd.concat(norm_data)


def analyse_back_prop(files, start_time):
    full = []
    for f in files:
        md = get_metadata(f)
        step = get_from_dict(md, ['qmc', 'nmeasure'])
        dt = get_from_dict(md, ['qmc', 'dt'])
        tbp = get_from_dict(md, ['estimators', 'estimators', 'back_prop', 'tau_bp'])
        start = min(1, int(start_time/tbp) + 1)
        data = extract_data(f, 'back_propagated', 'energies')[start:]
        av = data.mean().to_frame().T
        err = (data.std() / len(data)**0.5).to_frame().T
        res = pd.merge(av,err,left_index=True,right_index=True,suffixes=('','_error'))
        columns = set_info(res, md)
        full.append(res)
    return pd.concat(full).sort_values('tau_bp')

def analyse_estimates(files, start_time, multi_sim=False, av_tau=False):
    mds = []
    basic = []
    if av_tau:
        data = []
        for f in files:
            data.append(extract_mixed_estimates(f))
        full = pd.concat(data).groupby('Iteration')
        av = average_tau(full)
        print(av.apply(numpy.real).to_string())
    else:
        for f in files:
            md = get_metadata(f)
            read_rs = get_from_dict(md, ['psi', 'read_rs'])
            step = get_from_dict(md, ['qmc', 'nsteps'])
            dt = get_from_dict(md, ['qmc', 'dt'])
            fp = get_from_dict(md, ['propagators', 'free_projection'])
            start = int(start_time/(step*dt)) + 1
            if read_rs:
                start = 0
            data = extract_mixed_estimates(f, start)
            columns = set_info(data, md)
            basic.append(data.drop('Iteration', axis=1))
            mds.append(md)

        new_columns = []
        for c in columns:
            if (c == "E_T"):
                continue
            else:
                new_columns += [c]
        columns = new_columns
        basic = pd.concat(basic).groupby(columns)

        if fp:
            basic_av = reblock_free_projection(basic, columns)
        else:
            basic_av = reblock_mixed(basic, columns)

        base = files[0].split('/')[-1]
        outfile = 'analysed_' + base
        fmt = lambda x: "{:13.8f}".format(x)
        print(basic_av.to_string(index=False, float_format=fmt))
        with h5py.File(outfile, 'w') as fh5:
            fh5['metadata'] = numpy.array(mds).astype('S')
            try:
                fh5['basic/estimates'] = basic_av.drop('integrals',axis=1).values.astype(float)
            except:
                print("No integral attribute found")
            fh5['basic/headers'] = numpy.array(basic_av.columns.values).astype('S')
