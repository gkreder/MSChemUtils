############################################################
import sys
import os
import numpy as np
from tqdm.auto import tqdm
import molmass
import pandas as pd
import scipy.stats
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
from adjustText import adjust_text
import argparse
from joblib import Parallel, delayed
import pickle as pkl
import sys
import os
import numpy as np
import warnings
from pyteomics import mzml as pytmzml
import xml.etree.ElementTree as ET
############################################################
import formulaUtils
import plotUtils
############################################################

quasiCutoffDefault = 5

def scrape_spectra_hits(**kwargs):
    # The mzML namespace
    ns = {'mzml': 'http://psi.hupo.org/ms/mzml'}
    inFiles = kwargs['inFiles'].split(",")
    hit_rows = []
    bRT = float(kwargs['Begin'])
    eRT = float(kwargs['End'])
    target_isolation_window_mz = float(kwargs['Targeted m/z'])
    isolation_tol = float(kwargs['isolationMZTol'])
    for inFile in tqdm(inFiles):
        if inFile.startswith('"'):
            inFile = inFile[1 : ]
        if inFile.endswith('"'):
            inFile = inFile[ : -1]
        tree = ET.parse(inFile)
        root = tree.getroot()
        for spectrum in root.findall('.//mzml:spectrum', ns):
            # scan_start_time = None
            isolation_window_mz = None
            # Look for a cvParam with accession
            for cvParam in spectrum.findall('.//mzml:cvParam', ns):
                accession = cvParam.get('accession')
                if accession == 'MS:1000511':  # MS level
                    ms_level = cvParam.get('value')
                elif accession == 'MS:1000016':  # Scan start time (minutes)
                    scan_start_time = float(cvParam.get('value'))
                elif accession == 'MS:1000827':  # Isolation window target m/z
                    isolation_window_mz = float(cvParam.get('value'))
            # Check if the conditions are met
            if ms_level == "2" and (bRT <= scan_start_time <= eRT) and (abs(isolation_window_mz - target_isolation_window_mz) <= isolation_tol):
                # Get the id attribute of the spectrum
                spectrum_native_id = spectrum.get('id')
                spectrum_index = spectrum.get("index")
                hit_rows.append((inFile, spectrum_native_id, spectrum_index, scan_start_time))
    return(hit_rows)

def quasi_convert(d, **kwargs):
    args = argparse.Namespace(**kwargs)
    # Conversion to quasicounts by dividing the intensity by gamma_i(1 + delta)
    d = d.copy()
    d['quasi'] = d['intensity'] / ( args.quasiX *  ( np.power(d['mz'], args.quasiY) ) )
    return(d)

def filter_data(d, filter, **kwargs):
    args = argparse.Namespace(**kwargs)
    d = d.copy()
    if len(d) == 0:
        return(d)
    if filter == 'absolute':
        out_d = d.loc[lambda x : x['intensity'] >= args.absCutoff]
    elif filter == "pdpl":
        diffSearch = np.min(np.abs(d['mz'][:, np.newaxis] - args.prePeaks), axis = 1)
        indices = np.where(diffSearch <= args.matchAcc)
        out_d = d.iloc[indices]
    elif filter == 'quasi':
        out_d = d.loc[lambda x : x['quasi'] >= args.quasiCutoff]
    elif filter == 'parent_mz':
        out_d = d.loc[lambda x : x['mz'] <= ( args.parentMZ + args.matchAcc )]
    elif filter == 'match_acc':
        out_d = d.copy()
        # Eliminating all peaks within match accuracy of peak exclusion list peaks
        for mze in args.excludePeaks:
            out_d = out_d.loc[lambda x : np.abs(x['mz'] - mze) > args.matchAcc]
    elif filter == 'relative':
        indices = np.where((d['intensity'] / max(d['intensity'])) >= args.relCutoff)
        out_d = d.iloc[indices]
    elif filter == 'res_clearance':
        mzs = d['mz'].copy().values
        out_d = d.copy()
        for mz in mzs:
            if mz not in out_d['mz'].values:
                continue
            # out_d = out_d.loc[lambda x : np.abs(x['mz'] - mz) > args.resClearance]    
            out_d = pd.concat([out_d.loc[lambda x : x['mz'] == mz], out_d.loc[lambda x : np.abs(x['mz'] - mz) > args.resClearance]]).reset_index(drop = True)
    return(out_d)

def H(x):
    h = -1 * np.sum( x * ( np.log(x) ) )
    return(h)

def run_matching(args):
    """
    :param mzml1: Path to .mzML file
    :type mzml1: string
    :param mzml2: Path to .mzML file
    :type mzml2: string
    :param index1: Index of spectrum in mzml1
    :type index1: int
    :param index2: Index of spectrum in mzml2
    :type index2: int
    :param quasiX:
    :type quasiX: float
    :param quasiY:
    :type quasiY: float
    :param outDir: Output directory
    :type outDir: str
    :param parentMZ: Parent m/z
    :type parentMZ: float
    :param R:
    :type R: float
    :param parentFormula: Optionally provided parent formula, defaults to None
    :type parentFormula: str, optional
    :parmam absCutoff: Absolute intensity cutoff, defaults to 0
    :type absCutoff: float, optional
    :parmam relCutoff: Relative intensity cutoff, defaults to 0
    :type relCutoff: float, optional
    :param DUMin: defaults to -0.5
    :type DUMin: float, optional
    :param PEL: Peak exclusion list. Only one of PEL or PDPL can be given, defaults to None
    :type PEL: str, optional
    :param PDPL: Predefined peak list. Only one of PEL or PDPL can be given, defaults to None
    :type PDPL: str, optional
    :param startingIndex: Starting index for picking spectrum, defaults to 0
    :type startingIndex: int, optional
    :param gainControl: defaults to False
    :type gainControl: bool, optional
    :param quasiCutoff: Quasicount cutoff (note the odd default value implementation), defaults to 5
    :type quasiCutoff: float, optional
    :param minSpectrumQuasiCounts: Minimum spectrum quasi counts, defaults to 20
    :type minSpectrumQuasiCounts: float, optional
    :param minTotalPeaks: Minimum total peaks required (after filtering), defaults to 2
    :type minTotalPeaks: float, optional
    :param outPrefix: Output prefix to use instead of default filenames if provided, defaults to None
    :type outPrefix: str, optional
    :param silent: Run in silent mode with no text output, defaults to False
    :type silent: bool, optional
    :param no_log_plots: Dont produce any log plots, defaults to False
    :type no_log_plots: bool, optional
    :param no_matching_results: If set to true, just returns the statistics dataframe without writing output and exits, defaults to False
    :type no_matching_results: bool, optional
    :param intersection_only: If set to True only runs the intersection (not union) case, defaults to False
    :type intersection_only: bool, optional
    """

    args.resClearance = 200 / args.R
    args.resClearance = float(args.resClearance)
    args.matchAcc = 100 / args.R
    args.matchAcc = float(args.matchAcc)
    args.subFormulaTol = 100 / args.R

    args.index1 = args.index1 - args.startingIndex
    args.index2 = args.index2 - args.startingIndex

    # Matching accuracy m/z tolerance must be <= 0.5 x (resolution clearance width) or the peak matching will break
    if args.matchAcc > ( args.resClearance * 0.5):
        sys.exit('Error - the matching accuracy (matchAcc) must at most 0.5 * resolution clearance width (resClearance)')

    if args.quasiY > 0:
        sys.exit('Error - quasiY must be a non-positive number')

    if str(args.DUMin).capitalize() == "None":
        args.DUMin = None

    if args.PEL != None and args.PDPL != None:
        sys.exit("\nError - can only input either a Peak exclusion list or a Predefined peak list but not both\n")

    if args.PDPL != None and args.quasiCutoff == quasiCutoffDefault:
        args.quasiCutoff = 0

    if args.PEL != None:
        with open(args.PEL, 'r') as f:
            excludePeaks = [float(x.strip()) for x in f.readlines()]
        args.excludePeaks = excludePeaks

    if args.PDPL != None:
        with open(args.PDPL, 'r') as f:
            prePeaks = np.array([float(x.strip()) for x in f.readlines()])
            prePeaks = sorted(prePeaks)
            pDiffs = np.diff(prePeaks)
            badDiffs = np.where(pDiffs <= args.resClearance)[0]
            if len(badDiffs) > 0:
                sys.exit(f"\nError - Some Predefined peak list m/z values are spaced too closely for given resolution width e.g. {prePeaks[badDiffs[0]]} and {prePeaks[badDiffs[0] + 1]}\n")
        args.prePeaks = prePeaks


    suf1 = os.path.splitext(os.path.basename(os.path.basename(args.mzml1)))[0]
    suf2 = os.path.splitext(os.path.basename(os.path.basename(args.mzml2)))[0]
    ind1 = args.index1 + args.startingIndex
    ind2 = args.index2 + args.startingIndex
    if args.parentFormula == None:
        formString = "noFormula"
    else:
        formString = "formula"
    if not args.outPrefix:
        baseOutFileName = f"{suf1}_Scan_{ind1}_vs_{suf2}_Scan_{ind2}_{formString}"
    else:
        baseOutFileName = f"{args.outPrefix}"

    ############################################################
    # Spectrum Filtering
    ############################################################
    # outSuff = "230206_output_quasi5_20V_V9"
    mkdir_cmd = f"mkdir -p {args.outDir}"
    os.system(f'{mkdir_cmd}')
    os.chmod(args.outDir, 0o777)
    with open(os.path.join(args.outDir, 'argsFiltering.txt'), 'w') as f:
        print(vars(args), file = f)


    data = [{}, {}]
    for i_d, d in enumerate(data):
        reader = pytmzml.MzML([args.mzml1, args.mzml2][i_d])
        spec = reader.get_by_index([args.index1, args.index2][i_d])
        reader.close()
        d['spec'] = spec
        if 'negative scan' in spec.keys():
            polarity = 'Negative'
        elif 'positive scan' in spec.keys():
            polarity = 'Positive'
        d['polarity'] = polarity
        if args.gainControl:
            sys.exit('Error - havent implemented Gain Control for pyteomics implementation')
            injTime = spec.getAcquisitionInfo()[0].getMetaValue('MS:1000927')
            if injTime == None:
                sys.exit(f"Error - gain control set to True but spectrum {i_d + 1} has no injection time in its header")
            d['injection time'] = injTime
        mzs = spec['m/z array']
        ints = spec['intensity array']
        d['mz'] = mzs
        d['intensity'] = ints
    dataBackup = [x.copy() for x in data]
        
    # pyms.IonSource.Polarity.NEGATIVE
    if len(set([d['polarity'] for d in data])) != 1:
        sys.exit('Error - the spectra polarities must match')
    # polarity = {pyms.IonSource.Polarity.NEGATIVE : "Negative", pyms.IonSource.Polarity.POSITIVE : "Positive"}[set([d['polarity'] for d in data]).pop()]
    # polarity = ['Negative', 'Positive'][set([d['polarity'] for d in data]).pop()]

    if set([d['spec']['ms level'] for d in data]) != set([2]):
        for i_d, d in enumerate(data):
            if d['spec']['ms level'] != 2:
                sys.exit(f'Error - spectrum {i_d + 1} has MS level {d["spec"]["ms level"]}')

    #######################################################
    #######################################################
    grayData = []
    for i_d, d in enumerate(data):
        if not args.silent:
            mzmlName = [args.mzml1, args.mzml2][i_d]
            indexName = [args.index1, args.index2][i_d]
            print(f"---- Prepping Spectrum {i_d + 1} {mzmlName} {indexName} ----")
        if args.gainControl:
            injTime = d['injection time']
            d['uncorrected intensity'] = d['intensity'].copy()
            d['intensity'] = d['intensity'] * injTime
        # --------------------------------------------
        # Basic spectrum clean-up and filtering
        # --------------------------------------------
        if not args.silent:
            print("Filtering peaks....")
        # d = pd.DataFrame(d)
        d = pd.DataFrame({'mz' : d['mz'], 'intensity' : d['intensity']}) # 2023-09-07
        # Absolute intensity cutoff
        d = filter_data(d, 'absolute', **vars(args))
        # Quasicount conversion
        d = quasi_convert(d, **vars(args))
        grayData.append(d.copy())
        if args.PDPL != None: # Step 11 of the word template
            # Eliminating all peaks not within match accuracy of a peak in the pre-defined peak list
            d = filter_data(d, 'pdpl', **vars(args))
        # Eliminating peaks with quasicount < quasicount intensity cutoff
        d = filter_data(d, 'quasi', **vars(args)) 
        # Eliminating peaks with m/z > parent peak m/z
        d = filter_data(d, 'parent_mz', **vars(args))
        grayData[i_d] = filter_data(grayData[i_d], 'parent_mz', **vars(args)) # For plotting purposes
        # Eliminating all peaks within match accuracy of peak exclusion list peaks
        if args.PEL != None and args.PDPL == None:
            d = filter_data(d, 'match_acc', **vars(args))
        # Eliminate all peaks with normalized intensity < relative intensity cutoff
        d = filter_data(d, 'relative', **vars(args))
        # Sort remaining peaks in spectrum from most to least intense
        d = d.sort_values(by = 'intensity', ascending = False)
        # For each peak (from most intense to least), eliminate any peaks within the closed interval [m/z +- resolution clearance]
        d = filter_data(d, 'res_clearance', **vars(args))
        # Note - these filters were originally intended for the CDF plots
        if not (len(d['quasi']) > 0 and d['quasi'].sum() >= args.minSpectrumQuasiCounts and len(d['quasi']) >= args.minTotalPeaks):
            errorFile = os.path.join(args.outDir, f"{baseOutFileName}.log")
            with open(errorFile, 'w') as f:
                if len(d['quasi']) == 0:
                    print('The number of quasicounted peaks equals 0', file = f)
                if d['quasi'].sum() < args.minSpectrumQuasiCounts:
                    print(f"The spectrum quasicount sum ({d['quasi'].sum()}) did not exceed the minimum required ({args.minSpectrumQuasiCounts})", file = f)
                if len(d['quasi']) < args.minTotalPeaks:
                    print(f"There were too few peaks ({len(d['quasi'])}) compared to the required minimum ({args.minTotalPeaks})", file = f)
            return
        data[i_d] = d.copy().reset_index(drop = True)
        grayData[i_d] = grayData[i_d].copy().reset_index(drop = True)
        # sys.exit('gkreder - I put this in here to stop empty spectra. Refer to 2022-11-25_comparisonSpcetrumFiltering.ipynb')
    dfs = []
    for df in data:
        df = df.sort_values(by = 'mz').reset_index(drop = True)
        df['mz_join'] = df['mz']
        dfs.append(df)
        

    ############################################################
    # P-Value Calculation
    ############################################################
    def calc_G2(a_i, b_i, S_A, S_B, M):
        t1 = ( a_i + b_i ) * np.log( ( a_i + b_i ) / ( S_A + S_B ) ).apply(lambda x : 0 if np.isinf(x) else x)
        t2 = a_i * np.log(a_i / S_A).apply(lambda x : 0 if np.isinf(x) else x)
        t3 = b_i * np.log(b_i / S_B).apply(lambda x : 0 if np.isinf(x) else x)
        G2 = -2 * (t1 - t2 - t3).sum()
        pval_G2 = scipy.stats.chi2.sf(G2, df = M - 1)
        return(G2, pval_G2)

    def calc_D2(a_i, b_i, S_A, S_B, M):
        num = np.power( ( S_B * a_i )  - ( S_A * b_i ) , 2) 
        denom = a_i + b_i
        D2 = np.sum(num  / denom) / (S_A * S_B)
        
        pval_D2 = scipy.stats.chi2.sf(D2, df = M - 1)
        return(D2, pval_D2)

    def calc_SR(a_i, b_i, S_A, S_B):
        num = ( S_B * a_i ) - ( S_A * b_i )
        denom = np.sqrt(S_A * ( S_A + S_B ) * ( a_i + b_i ))
        SR_ai = num / denom

        num = ( S_A * b_i ) - ( S_B * a_i )
        denom = np.sqrt(S_B * ( S_A + S_B ) * ( a_i + b_i ))
        SR_bi = num / denom

        return((SR_ai, SR_bi))

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        df = pd.merge_asof(dfs[0], dfs[1], tolerance = args.matchAcc, on = 'mz_join', suffixes = ('_A', '_B'), direction = 'nearest').drop(columns = 'mz_join')
        for i, suf in enumerate(['A', 'B']):
            dft = dfs[i]
            dfRem = dft[~dft['mz'].isin(df[f'mz_{suf}'])]
            dfRem = dfRem.rename(columns = {x : f"{x}_{suf}" for x in ['mz', 'intensity', 'formula', 'm/z_calculated', 'quasi']}).drop(columns = 'mz_join') #quasi?
            df = pd.concat([df, dfRem])
        df = df.reset_index(drop = True)
        # Mapping parent formulas using the new multi-formula version
        if args.parentFormula != None:
            # print('Formula mapping...')
            form = molmass.Formula(args.parentFormula).formula

            allForms = formulaUtils.generateAllForms(form)

            formulas = [None for x in range(len(df))]
            formulaMasses = [None for x in range(len(df))]
            
            for i, (mz_a, mz_b) in enumerate(df[['mz_A', 'mz_B']].values):
                if np.isnan(mz_a):
                    # bestForm, thMass, error = formulaUtils.findBestForm(mz_b, form, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                    bestForms, thMasses, errors = formulaUtils.findBestForms(mz_b, allForms, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                elif np.isnan(mz_b):
                    # bestForm, thMass, error = formulaUtils.findBestForm(mz_a, form, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                    bestForms, thMasses, errors = formulaUtils.findBestForms(mz_a, allForms, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                else:
                    # bestForm, thMass, error = formulaUtils.findBestForm(np.mean((mz_a, mz_b)), form, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                    bestForms, thMasses, errors = formulaUtils.findBestForms(np.mean((mz_a, mz_b)), allForms, toleranceDa = args.subFormulaTol, DuMin=args.DUMin)
                bestForm = bestForms[0]
                if bestForm == None:
                    continue
                formulas[i] = ", ".join([str(x).replace("None", "") for x in bestForms])
                formulaMasses[i] = ", ".join([str(x) for x in thMasses])


            df['formula'] = np.array(formulas)
            df['m/z_calculated'] = np.array(formulaMasses)
            if args.PDPL == None:
                df = df.dropna(subset = [f'formula'])

        stats = {"Union" : {}, "Intersection" : {}}
        # stats = {"Intersection" : {}}
        dfCs = {}
        jaccardTemp = {}
        for join in stats.keys():
            if join == 'Union':
                dfC = df.copy()
                # if args.intersection_only:
                    # continue
            elif join == 'Intersection':
                dfC = df.dropna(subset = ['mz_A', 'mz_B']).copy()
            
            
            a_i = dfC['quasi_A']
            b_i = dfC['quasi_B']
            M = dfC.shape[0]
            jaccardTemp[join] = M
            stats[join]['quasi_A'] = dfC['quasi_A'].sum()
            stats[join]['quasi_B'] = dfC['quasi_B'].sum()
            stats[join]['M'] = M

            # S_A = dfC['intensity_A'].sum()
            # S_B = dfC['intensity_B'].sum()
            S_A = dfC['quasi_A'].sum()
            S_B = dfC['quasi_B'].sum()
            stats[join]['S_A'] = S_A
            stats[join]['S_B'] = S_B
            stats[join]['S_A (raw)'] = dfC['intensity_A'].sum()
            stats[join]['S_B (raw)'] = dfC['intensity_B'].sum()


            

            # Calculate D^2
            D2, pval_D2 = calc_D2(a_i.fillna(value = 0.0), b_i.fillna(value = 0.0), S_A, S_B, M)
            stats[join]['D^2'] = D2
            stats[join]['pval_D^2'] = pval_D2

            # Calculate G^2
            G2, pval_G2 = calc_G2(a_i.fillna(value = 0.0), b_i.fillna(value = 0.0), S_A, S_B, M)
            stats[join]['G^2'] = G2
            stats[join]['pval_G^2'] = pval_G2


            # p_Ai = a_i / S_A
            # p_Bi = b_i / S_B
            p_Ai = a_i / dfC['quasi_A'].sum()
            p_Bi = b_i / dfC['quasi_B'].sum()
            

            # Calculate the Spectrum Entropy and perplexity
            H_pA = H(p_Ai)
            H_pB = H(p_Bi)

            PP_pA = np.exp(H_pA)
            PP_pB = np.exp(H_pB)
            stats[join]['Entropy_A'] = H_pA
            stats[join]['Entropy_B'] = H_pB
            stats[join]['Perplexity_A'] = PP_pA
            stats[join]['Perplexity_B'] = PP_pB


            # Jensen-Shannon divergence
            sTerm = (p_Ai + p_Bi).fillna(value = 0)
            JSD = H( ( 0.5 * ( p_Ai.fillna(0.0) + p_Bi.fillna(0.0) ) ) ) - ( 0.5 * H_pA ) - ( 0.5 *  H_pB  ) 
            stats[join]['JSD'] = JSD

            # Cosine Distance
            def sqF(x):
                return(np.sqrt( np.power( x, 2 ).sum() ) )
            num = (p_Ai * p_Bi).sum()
            denom = sqF(p_Ai) * sqF(p_Bi)
            # CSD = 1 - ( num / denom )
            CSD = ( num / denom )
            stats[join]['Cosine Similarity'] = CSD

            dfC[f"D^2_{join}"] = None
            dfC[f"G^2_{join}"] = None
            dfC[f"SR_A_{join}"] = None
            dfC[f"SR_B_{join}"] = None

            for i_row, row in dfC.iterrows():
                a_i = row['quasi_A']
                b_i = row['quasi_B']
                if ((np.isnan(a_i)) or (np.isnan(b_i))) and (join == 'Intersection'): 
                    continue
                a_i = pd.Series([a_i])
                b_i = pd.Series([b_i])
                D2, _ = calc_D2(a_i.fillna(value = 0.0), b_i.fillna(value = 0.0), S_A, S_B, M)
                G2, _ = calc_G2(a_i.fillna(value = 0.0), b_i.fillna(value = 0.0), S_A, S_B, M)
                # Calculate Standardized Residual (SR)
                SR_ai, SR_bi = calc_SR(a_i.fillna(value = 0.0), b_i.fillna(value = 0.0), S_A, S_B)
                SR_ai = SR_ai.values[0]
                SR_bi = SR_bi.values[0]
                dfC.at[i_row, f"D^2_{join}"] = D2
                dfC.at[i_row, f"G^2_{join}"] = G2
                dfC.at[i_row, f"SR_A_{join}"] = SR_ai
                dfC.at[i_row, f"SR_B_{join}"] = SR_bi
            dfCs[join] = dfC

        jaccard = jaccardTemp['Intersection'] / jaccardTemp['Union']
        stats['Union']['Jaccard'] = jaccard 
        if args.parentFormula == None:
            pft = "Not specified"
        else:
            pft = args.parentFormula 
        stats['Union']['Precursor formula'] = pft
        stats['Union']["Precursor m/z"] = args.parentMZ
        stats['Union']['Polarity'] = polarity
        if args.quasiY == 0.0:
            stats['Union']['Quasicount scaling function'] = f"{args.quasiX}"
        else:
            stats['Union']['Quasicount scaling function'] = f"{args.quasiX} x [m/z]^{args.quasiY}"

    df = pd.merge(dfCs['Union'], dfCs['Intersection'], how = 'outer')


    dfOut = df.rename(columns = {'mz_A' : 'm/z_A',
    'mz_B' : 'm/z_B',
    "intensity_A" : "I_A (raw intensity)",
    "intensity_B" : "I_B (raw intensity)",
    "quasi_A" : "a (quasicounts)",
    "quasi_B" : "b (quasicounts)",
    "formula" : "Formula"
    })

    if args.parentFormula == None:
        dfOut = dfOut[['m/z_A', 'm/z_B', 'I_A (raw intensity)', 'I_B (raw intensity)', 'a (quasicounts)', 'b (quasicounts)', "D^2_Union", "G^2_Union", "D^2_Intersection", "G^2_Intersection", "SR_A_Union", "SR_A_Intersection",  "SR_B_Union", "SR_B_Intersection"]]
    else:
        # dfOut = dfOut[['formula_A', 'formula_B', 'm/z_a', 'm/z_b', 'I_a', 'I_b', 'a', 'b', "D^2_Union", "G^2_Union", "D^2_Intersection", "G^2_Intersection"]]
        dfOut = dfOut[['Formula', 'm/z_A', 'm/z_B', 'I_A (raw intensity)', 'I_B (raw intensity)', 'a (quasicounts)', 'b (quasicounts)', "D^2_Union", "G^2_Union", "D^2_Intersection", "G^2_Intersection", "SR_A_Union", "SR_A_Intersection",  "SR_B_Union", "SR_B_Intersection"]]


    dfStats = pd.DataFrame(stats)
    if len(dfCs['Intersection']) <= 1: # Filter out based on M = 0 or 1
        # sys.exit("gkreder - put this in outside of loop context. See 2022-11-25_comparisonPvalCalc.ipynb for original error")
        errorFile = os.path.join(args.outDir, f"{baseOutFileName}.log")
        with open(errorFile, 'w') as f:
            s = ""
            if args.parentFormula:
                s += " and parent formula filtering "
            print(f"There were too few peaks after merging spectra{s}({len(dfCs['Intersection'])})", file = f)
        return

    if args.no_matching_results:
        return(dfStats)
    
    outExcelFile = os.path.join(args.outDir, f"{baseOutFileName}.xlsx")
    writer = pd.ExcelWriter(outExcelFile, engine = 'xlsxwriter')
    dfStats.drop(labels =['quasi_A', 'quasi_B'], axis = 0).rename(index = {'S_A' : 's_A (quasicounts)', 'S_B' : 's_B (quasicounts)'}).to_excel(writer, sheet_name = "Stats")
    dfOut.drop(labels = [x for x in dfOut.columns if "_Union" in x], axis = 1).dropna(subset = ['m/z_A', 'm/z_B']).to_excel(writer, sheet_name = "Spectra_Intersection", index = False)
    dfOut.drop(labels = [x for x in dfOut.columns if "_Intersection" in x], axis = 1).to_excel(writer, sheet_name = "Spectra_Union", index = False)
    for isuf, suf in enumerate(['A', 'B']):
        pd.DataFrame({f"m/z_{suf}" : dataBackup[isuf]['mz'], f"I_{suf} (raw intensity)" : dataBackup[isuf]['intensity']}).to_excel(writer, sheet_name = f"Spectrum_{suf}_Raw", index = False) 
    writer.close()


    for plotJoin in ["Union", "Intersection"]:
        if plotJoin == "Intersection":
            dfPlot = df.dropna(subset = ['mz_A', 'mz_B'])
        elif plotJoin == 'Union':
            dfPlot = df.copy()
            if args.intersection_only:
                continue
        sideText = ""
        if args.parentFormula != None:
            sideText += f"Parent Formula : {args.parentFormula}"
        sideText += f"\nParent m/z : {args.parentMZ:.5f}"
        sideText += f"\nM {plotJoin} : {int(dfStats[plotJoin].loc['M'])}"
        sideText += f"\np-val (D^2) : {dfStats[plotJoin].loc['pval_D^2']:.2e}"
        sideText += f"\np-val (G^2) : {dfStats[plotJoin].loc['pval_G^2']:.2e}"
        sideText += f"\ns_A (quasi) : {dfStats[plotJoin].loc['quasi_A']:.2e}"
        sideText += f"\ns_B (quasi) : {dfStats[plotJoin].loc['quasi_B']:.2e}"
        sideText += f"\nH(p_A) : {dfStats[plotJoin].loc['Entropy_A']:.2e}"
        sideText += f"\nH(p_B) : {dfStats[plotJoin].loc['Entropy_B']:.2e}"
        sideText += f"\nPP(p_A) : {dfStats[plotJoin].loc['Perplexity_A']:.2e}"
        sideText += f"\nPP(p_B) : {dfStats[plotJoin].loc['Perplexity_B']:.2e}"
        sideText += f"\ncos(p_A, p_B) : {dfStats[plotJoin].loc['Cosine Similarity']:.2e}"
        sideText += f"\nJSD(p_A, p_B) : {dfStats[plotJoin].loc['JSD']:.2e}"
        if plotJoin == "Intersection":
            sideText += f"\nJaccard : {dfStats['Union'].loc['Jaccard']:.2e}"
        else:
            sideText += "\n"
        
        # Normalized 0 to 1 scale
        fig, ax = plt.subplots(figsize = (12,9))
        plotUtils.mirrorPlot(dfPlot['mz_A'], dfPlot['mz_B'], dfPlot['intensity_A'], 
                             dfPlot['intensity_B'], None, None, normalize = True, 
                             sideText = sideText, fig = fig, ax = ax)
        ax.set_xlim([0, ax.get_xlim()[1]])
        ylim = ax.get_ylim()
        xlim = ax.get_xlim()
        ylimMax = max([abs(x) for x in ylim])
        ylimRange = ylim[1] - ylim[0]
        plt.text(xlim[1] + ( ( xlim[1] - xlim[0] ) *  0.025 ), ylimMax - ( 0.050 * ylimRange ), f"{plotJoin}:", fontsize = 20)
        plt.text(xlim[0] + ( ( xlim[1] - xlim[0] ) *  0.01 ), ylimMax - ( .03 * ylimRange ), "Spectrum A", fontsize = 15, fontfamily = 'DejaVu Sans')
        plt.text(xlim[0] + ( ( xlim[1] - xlim[0] ) *  0.01 ), -ylimMax + ( .03 * ylimRange ), "Spectrum B", fontsize = 15, fontfamily = 'DejaVu Sans')
        plotFilePair = os.path.join(args.outDir, f"{baseOutFileName}_{plotJoin}_plot.svg")
        plot_title = f"{suf1} Scan {ind1} (A) vs.\n {suf2} Scan {ind2} (B) [{plotJoin}]"
        plt.title(f"{plot_title}")
        plt.ylabel("Relative intensity (quasicounts)")
        plt.savefig(plotFilePair, bbox_inches = 'tight') 
        plt.close()

        if not args.no_log_plots:
            gda, gdb = grayData
            fig, ax = plotUtils.mirrorPlot(gda['mz'], gdb['mz'], np.log10(gda['quasi']), np.log10(gdb['quasi']), None, None, normalize = False, sideText = sideText, overrideColor = "gray")
            plotUtils.mirrorPlot(dfPlot['mz_A'], dfPlot['mz_B'], np.log10(dfPlot['quasi_A']), 
                                        np.log10(dfPlot['quasi_B']), None, None, 
                                        normalize = False, sideText = sideText,
                                        fig = fig, ax = ax)
            ax.set_xlim([0, ax.get_xlim()[1]])
            ylim = ax.get_ylim()
            xlim = ax.get_xlim()
            ylimMax = max([abs(x) for x in ylim])
            ylimRange = ylim[1] - ylim[0]
            plt.text(xlim[1] + ( ( xlim[1] - xlim[0] ) *  0.025 ), ylimMax - ( 0.050 * ylimRange ), f"{plotJoin}:", fontsize = 20)
            plt.text(xlim[0] + ( ( xlim[1] - xlim[0] ) *  0.01 ), ylimMax - ( .03 * ylimRange ), "Spectrum A", fontsize = 15, fontfamily = 'DejaVu Sans')
            plt.text(xlim[0] + ( ( xlim[1] - xlim[0] ) *  0.01 ), -ylimMax + ( .03 * ylimRange ), "Spectrum B", fontsize = 15, fontfamily = 'DejaVu Sans')
            plotFilePair = os.path.join(args.outDir, f"{baseOutFileName}_{plotJoin}_quasicount_log_plot.svg")
            plot_title = f"{suf1} Scan {ind1} (A) vs.\n {suf2} Scan {ind2} (B) [{plotJoin}]"
            plt.title(f"{plot_title}")
            plt.ylabel("Log10 absolute intensity (quasicounts)")
            ax.set_ylim((-ylimMax, ylimMax))
            plt.savefig(plotFilePair, bbox_inches = 'tight') 
            plt.close()
    return(dfStats)

def get_args(arg_string = None):

    parser = argparse.ArgumentParser()
    parser.add_argument("--mzml1", required = True)
    parser.add_argument("--index1", required = True, type = int)
    parser.add_argument("--mzml2", required = True)
    parser.add_argument("--index2", required = True, type = int)
    parser.add_argument("--quasiX", required = True, type = float)
    parser.add_argument("--quasiY", required = True, type = float)
    parser.add_argument("--absCutoff", default = 0, type = float)
    parser.add_argument("--relCutoff", default = 0, type = float)
    parser.add_argument("--DUMin", default = -0.5, type = float)
    parser.add_argument("--PEL", default = None)
    parser.add_argument("--PDPL", default = None)
    parser.add_argument("--startingIndex", default = 0, type = int)
    # parser.add_argument("--R", default = 10000, type = float)
    parser.add_argument("--R", required = True, type = float)
    parser.add_argument("--gainControl", default = False)
    parser.add_argument("--quasiCutoff", default = quasiCutoffDefault, type = float)
    parser.add_argument("--minSpectrumQuasiCounts", default = 20, type = float)
    parser.add_argument("--minTotalPeaks", default = 2, type = float)
    parser.add_argument("--outDir", required = True)
    parser.add_argument("--outPrefix", default = None)
    parser.add_argument("--parentMZ", required = True, type = float)
    parser.add_argument("--parentFormula", default = None)
    parser.add_argument("--silent", action = "store_true")
    parser.add_argument("--no_log_plots", action = "store_true")
    parser.add_argument("--no_matching_results", action = "store_true")
    parser.add_argument("--intersection_only", action = "store_true")


    if arg_string == None:
        args = parser.parse_args()
    else:
        args = parser.parse_args(arg_string.split(" "))
    
    return(args)



if __name__ == "__main__":
    args = get_args()
    run_matching(args)





 





