#!/usr/bin/env python
'''Run a reblocking analysis on AFQMCPY QMC output files. Heavily adapted from
HANDE'''

import pandas as pd
import pyblock
import analysis.extraction
import numpy
import matplotlib.pyplot as pl


def run_blocking_analysis(filename, start_iter):
    '''
'''

    (metadata, data) = analysis.extraction.extract_data(filename[0])
    (data_len, reblock, covariances) = pyblock.pd_utils.reblock(data.drop(['iteration',
                                                                           'time',
                                                                           'exp(delta)'],
                                                                           axis=1))
    cov = covariances.xs('Weight', level=1)['E_num']
    numerator = reblock.ix[:,'E_num']
    denominator = reblock.ix[:,'Weight']
    projected_energy = pyblock.error.ratio(numerator, denominator, cov, 4)
    projected_energy.columns = pd.MultiIndex.from_tuples([('Energy', col)
                                    for col in projected_energy.columns])
    reblock = pd.concat([reblock, projected_energy], axis=1)
    summary = pyblock.pd_utils.reblock_summary(reblock)
    useful_table = analysis.extraction.pretty_table(summary, metadata)

    return (reblock, useful_table)


def average_tau(filenames):

    data = analysis.extraction.extract_data_sets(filenames)
    frames = []

    for (m,d) in data:
        frames.append(d)

    frames = pd.concat(frames).groupby('iteration')
    data_len = frames.size()
    means = frames.mean()
    err = numpy.sqrt(frames.var())
    covs = frames.cov().loc[:,'E_num'].loc[:, 'Weight']
    energy = means['E_num'] / means['Weight']
    energy_err = abs(energy/numpy.sqrt(data_len))*((err['E_num']/means['E_num'])**2.0 +
                                   (err['Weight']/means['Weight'])**2.0 -
                                   2*covs/(means['E_num']*means['Weight']))**0.5

    pl.show()
    tau = m['qmc_options']['dt']
    nsites = m['model']['nx']*m['model']['ny']
    results = pd.DataFrame({'E': energy/nsites, 'E_error': energy_err/nsites}).reset_index()
    results['tau'] = results['iteration'] * tau

    return analysis.extraction.pretty_table_loop(results, m['model'])
