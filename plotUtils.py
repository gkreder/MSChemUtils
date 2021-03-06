# -------------------------------------------------------
import sys
import os
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from adjustText import adjust_text
from matplotlib.ticker import ScalarFormatter, FuncFormatter
import matplotlib.ticker 
# -------------------------------------------------------

def mirrorPlot( mzs_a, mzs_b, intensities_a, intensities_b, formulas_a = None, formulas_b = None, normalize = True, rotation = 90):
    plt.rcParams['font.size'] = 16
    fig, ax = plt.subplots(figsize = (12,9))
    # plt.stem(df['mz_A'], df['intensity_A'], linefmt = 'b-', markerfmt = ' ', basefmt = ' ')
    if normalize:
        intensities_a = intensities_a / intensities_a.max()
        intensities_b = intensities_b / intensities_b.max()
        # intensities_a = np.array([x / max(intensities_a) for x in intensities_a])
        # intensities_b = np.array([x / max(intensities_b) for x in intensities_b])
    vlinesA = ax.vlines(mzs_a, 0, intensities_a, color = '#67a9cf', alpha = 0.9, linewidth = 0.75)
    vlinesB = ax.vlines(mzs_b, 0, -intensities_b, color = '#ef8a62', alpha = 0.9, linewidth = 0.75)
    ax.axhline(0, 0, 1, color = 'black', alpha = 0.75, linewidth = 0.5)

    fig.canvas.draw()

    if normalize:
        @FuncFormatter
        def my_formatter(x, pos):
            return( "{:.2e}".format(abs(x)))
        ax.get_yaxis().set_major_formatter(my_formatter)
    
    ylim = ax.get_ylim()
    tAdjust = ( ylim[1] - ylim[0] ) * 0.02

    labelCutoff = 10
    texts = []

    if np.all(formulas_a) == None:
        formulas_a = [None for x in mzs_a]
    if np.all(formulas_b) == None:
        formulas_b = [None for x in mzs_b]
    packageA = sorted(zip(mzs_a, intensities_a, formulas_a), key = lambda x : x[1], reverse = True)
    packageB = sorted(zip(mzs_b, intensities_b, formulas_b), key = lambda x : x[1], reverse = True)
    for i_row in range(labelCutoff):
        mz_a, int_a, formula_a = packageA[i_row]
        mz_b, int_b, formula_b = packageB[i_row]
        # if i_row == labelCutoff:
            # break
        # texts.append(plt.text(row['mz_A'], row['intensity_A'] + tAdjust, row['formula_A'], ha = 'center'))
        if formula_a != None:
            texts.append(plt.text(mz_a, int_a, formula_a, ha = 'center', rotation = rotation))
        if formula_b != None:
            texts.append(plt.text(mz_b, -int_b, formula_b, ha = 'center', rotation = rotation))
        # texts.append(plt.text(row['mz_B'], - row['intensity_B'] - tAdjust, row['formula_B'], ha = 'center'))
        # texts.append(plt.text(row['mz_B'], - row['intensity_B'], row['formula_B'], ha = 'center'))
        # break

    adjust_text(texts, arrowprops=dict(arrowstyle='-', color='black'), ha = 'center') # , add_objects = [vlinesA, vlinesB]



    plt.xlabel('m/z')
    plt.ylabel('Intensity')
    return(fig, ax)


