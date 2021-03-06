# *-* coding: utf-8 *-*
# These lines were automatically added by the 3to2-conversion.
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals
# author   : Johann-Mattis List
# email    : mattis.list@gmail.com
# created  : 2013-03-12 11:56
# modified : 2014-12-02 21:57
"""
LexStat algorithm for automatic cognate detection.
"""

__author__="Johann-Mattis List"
__date__="2014-12-02"

from six import text_type
# builtin
import random
import codecs
import sys
from itertools import combinations_with_replacement
from math import factorial

# thirdparty
from six.moves import input
import numpy as np

# thirdparty modules
from ..thirdparty import cogent as cg

# lingpy-modules
from ..settings import rcParams
from ..sequence.sound_classes import ipa2tokens, tokens2class, prosodic_string, \
        prosodic_weights, class2tokens
from ..sequence.generate import MCPhon
from ..basic import Wordlist
from ..align.pairwise import turchin,edit_dist
from ..convert.strings import scorer2str
from ..read.phylip import read_scorer # for easy reading of scoring functions
from ..algorithm import clustering

from ..algorithm import calign
from ..algorithm import talign
from ..algorithm import misc
from .. import util


class LexStat(Wordlist):
    """
    Basic class for automatic cognate detection.

    Parameters
    ----------
    filename : str 
        The name of the file that shall be loaded.
    model : :py:class:`~lingpy.data.model.Model` 
        The sound-class model that shall be used for the analysis. Defaults to
        the SCA sound-class model.
    merge_vowels : bool (default=True)
        Indicate whether consecutive vowels should be merged into single tokens or kept
        apart as separate tokens.
    transform : dict
        A dictionary that indicates how prosodic strings should be simplified
        (or generally transformed), using a simple key-value structure with the
        key referring to the original prosodic context and the value to the new
        value.
        Currently, prosodic strings (see
        :py:meth:`~lingpy.sequence.sound_classes.prosodic_string`) offer 11
        different prosodic contexts. Since not all these are helpful in
        preliminary analyses for cognate detection, it is useful to merge some
        of these contexts into one. The default settings distinguish only 5
        instead of 11 available contexts, namely:

        * ``C`` for all consonants in prosodically ascending position,
        * ``c`` for all consonants in prosodically descending position, 
        * ``V`` for all vowels,
        * ``T`` for all tones, and 
        * ``_`` for word-breaks.

    check : bool (default=False)
        If set to c{True}, the input file will first be checked for errors
        before the calculation is carried out. Errors will be written to the
        file ``errors.log``.

    Notes
    -----
    Instantiating this class does not require a lot of parameters. However,
    the user may modify its behaviour by providing additional attributes in the
    input file.

    """
    
    def __init__(
            self,
            filename,
            **keywords
            ):

        kw = {
                "model"        : rcParams['sca'],
                "merge_vowels" : rcParams['merge_vowels'],
                'transform'    : rcParams['lexstat_transform'],
                "check"        : False,
                "apply_checks" : False,
                "defaults"     : False
                }
        kw.update(keywords)
        if kw['defaults']: return kw
        
        # store the model
        if str(kw['model']) == kw['model']:
            self.model = rcParams[kw['model']]
        else:
            self.model = kw['model']

        # set the lexstat stamp
        self._stamp = "# Created using the LexStat class of LingPy-2.0\n"

        # initialize the wordlist
        Wordlist.__init__(self,filename)
        
        # check for basic input data
        # tokens
        if not "tokens" in self.header:
            self.add_entries(
                    "tokens",
                    "ipa",
                    lambda x:ipa2tokens(
                        x,
                        merge_vowels = kw['merge_vowels']
                        )
                    )

        # add a debug procedure for tokens
        if kw["check"]:
            errors = []
            for key in self:
                line = self[key,"tokens"]
                if "" in line:
                    errors += [(
                        key,
                        "empty token",
                        ' '.join(line)
                        )]
                else:
                    try:
                        sonars = tokens2class(line,rcParams['art'])
                        if not sonars or sonars == ['0']:
                            errors += [(
                                key,
                                "empty sound-class string",
                                ' '.join(line)
                                )]
                    except:
                        errors += [(
                            key,
                            "sound-class conversion failed",
                            ' '.join(line)
                            )]
            if errors:
                out = codecs.open("errors.log","w",'utf-8')
                out.write("ID\tTokens\tError-Type\n")
                for a,b,c in errors:
                    out.write("{0}\t<{1}>\t{2}\n".format(a,c,b))
                out.close()
                if not kw["apply_checks"]:
                    answer = input(
                        "[?] There were errors in the input data. Do you want to exclude them? (y/n)")
                else:
                    answer = "y"

                if answer in rcParams['answer_yes']:
                    self.output(
                            'qlc',
                            filename=self.filename+'_cleaned',
                            subset=True,
                            rows = {"ID":"not in "+str([i[0] for i in errors])}
                            )
                    # load the data in another wordlist and copy the stuff
                    wl = Wordlist(self.filename+'_cleaned.qlc')
                    
                    # change the attributes
                    self._array = wl._array
                    self._data = wl._data
                    self._dict = wl._dict
                    self._idx = wl._idx

                    # store errors in meta
                    self._meta['errors'] = [i[0] for i in errors]

                else:
                    return
            else:
                self.log.info("No obvious errors found in the data.")

        # sonority profiles
        if not "sonars" in self.header:
            self.add_entries(
                    "sonars",
                    "tokens",
                    lambda x:[int(i) for i in tokens2class(
                        x,
                        rcParams['art'],
                        stress = rcParams['stress']
                        )]
                )

        # get prosodic strings
        if not "prostrings" in self.header:
            self.add_entries(
                    "prostrings",
                    "sonars",
                    lambda x:prosodic_string(x)
                    )

        # get sound class strings
        if not "classes" in self.header:
            self.add_entries(
                    "classes",
                    "tokens",
                    lambda x:''.join(tokens2class(x,kw["model"]))
                    )
        
        # create IDs for the languages
        if not "langid" in self.header:
            transform = dict(zip(self.taxa,[str(i+1) for i in range(self.width)]))
            self.add_entries(
                    "langid",
                    "taxa",
                    lambda x:transform[x]
                    )
        # get the numbers for all strings
        if not "numbers" in self.header:
            # change the discriminative potential of the sound-class string
            # tuples, note that this is still wip, we have to tweak around with
            # this in order to find an optimum for the calculation
            self._transform =  kw['transform']
            self.add_entries(
                    "numbers",
                    "langid,classes,prostrings",
                    lambda x,y: ["{0}.{1}.{2}".format(
                        x[y[0]],
                        a,
                        self._transform[b]
                        ) for a,b in zip(x[y[1]],x[y[2]])]    
                    )

        # check for weights
        if not "weights" in self.header:
            self.add_entries(
                    "weights",
                    "prostrings",
                    lambda x:prosodic_weights(x)
                    )

        # check for duplicates
        # first, check for item 'words' in data, if this is not given, create
        # it
        if 'ipa' in self.header:
            pass
        else:
            self.add_entries('ipa','tokens',lambda x:''.join(x))

        if not "duplicates" in self.header:
            duplicates = {}
            for taxon in self.taxa:
                words = []
                for idx in self.get_list(
                        col=taxon,
                        flat=True
                        ):
                    # get the words
                    word = self[idx,'ipa']
                    if word in words:
                        duplicates[idx] = 1
                    else:
                        duplicates[idx] = 0
                        words += [word]
            self.add_entries(
                    "duplicates",
                    duplicates,
                    lambda x:x
                    )

        # create an index 
        if not hasattr(self,'freqs'):
            self.chars = []
            self.rchars = []
            self.freqs = {}
            for taxon in self.taxa:
                self.freqs[taxon] = {}
                words = self.get_list(
                        col=taxon,
                        entry='numbers',
                        flat=True
                        )
                for word in words:
                    for char in word:
                        try:
                            self.freqs[taxon][char] += 1
                        except:
                            self.freqs[taxon][char] = 1
                        self.chars.append(char)
                        self.rchars.append(char[char.index('.')+1:])
            self.chars = list(set(self.chars))
            self.rchars = list(set(self.rchars))
            for i in range(self.width):
                self.chars += [str(i+1)+'.X.-']

        # check for scorers
        if not hasattr(self,"scorer"):
            self._meta['scorer'] = {}

        # create a scoring dictionary
        if not hasattr(self,"bscorer"):
            matrix = [[0.0 for i in range(len(self.chars))] for j in range(len(self.chars))]
            for i,charA in enumerate(self.chars):
                for j,charB in enumerate(self.chars):
                    if i < j:
                        
                        # add dictionary scores to the scoredict
                        score = self.model(
                                charA[charA.index('.')+1][0],
                                charB[charB.index('.')+1][0]
                                )
                        matrix[i][j] = score
                        matrix[j][i] = score
                    elif i == j:
                        # add dictionary scores to the scoredict
                        score = self.model(
                                charA[charA.index('.')+1][0],
                                charB[charB.index('.')+1][0]
                                )
                        matrix[i][j] = score
        
            self.bscorer = misc.ScoreDict(self.chars,matrix)
            self._meta['scorer']['bscorer'] = self.bscorer
        else:
            self.bscorer = self._meta['scorer']['bscorer']

        # check for rscorer
        if not hasattr(self,"rscorer"):
            matrix = [[0.0 for i in range(len(self.rchars))] for j in
                    range(len(self.rchars))]
            for i,charA in enumerate(self.rchars):
                for j,charB in enumerate(self.rchars):
                    if i < j:
                        
                        # add dictionary scores to the scoredict
                        score = self.model(
                                charA[0],
                                charB[0]
                                )
                        matrix[i][j] = score
                        matrix[j][i] = score
                    elif i == j:
                        # add dictionary scores to the scoredict
                        score = self.model(
                                charA[0],
                                charB[0]
                                )
                        matrix[i][j] = score
        
            self.rscorer = misc.ScoreDict(self.rchars,matrix)
            self._meta['scorer']['rscorer'] = self.rscorer
        else:
            self.rscorer = self._meta['scorer']['rscorer']

        # check for cscorer
        if 'scorer' in self._meta:
            if 'cscorer' in self._meta['scorer']:
                self.cscorer = self._meta['scorer']['cscorer']

        # make the language pairs
        if not hasattr(self,"pairs"):
            self.pairs = {}
            for i,taxonA in enumerate(self.taxa):
                for j,taxonB in enumerate(self.taxa):
                    if i < j:
                        self.pairs[taxonA,taxonB] = []

                        dictA = self.get_dict(col=taxonA)
                        dictB = self.get_dict(col=taxonB)

                        for c in dictA:
                            if c in dictB:
                                valA = dictA[c]
                                valB = dictB[c]

                                for idxA in valA:
                                    for idxB in valB:
                                        dA = self[idxA,"duplicates"]
                                        dB = self[idxB,"duplicates"]
                                        if dA != 1 and dB != 1:
                                            self.pairs[taxonA,taxonB] += [(idxA,idxB)]
                    elif i == j:
                        self.pairs[taxonA,taxonA] = []
                        dictAB = self.get_dict(col=taxonA)
                        for c in dictAB:
                            valAB = dictAB[c]
                            for idx in valAB:
                                dAB = self[idx,"duplicates"]
                                if dAB != 1:
                                    self.pairs[taxonA,taxonA] += [(idx,idx)]

    def __repr__(self):

        return "<lexstat-model {0}>".format(self.filename)

    def __getitem__(self,idx):
        """
        Method allows quick access to the data by passing the integer key.

        In contrast to the basic wordlist, the LexStat wordlist further allows
        to access item pairs by passing a tuple.
        """
        try:
            return self._cache[idx]
        except:
            pass
        
        try:
            # return full data entry as list
            out = self._data[idx]
            self._cache[idx] = out
            return out
        except KeyError:
            # check for dtype
            try:
                out = (
                        self._data[idx[0][0]][self._header[self._alias[idx[1]]]],
                        self._data[idx[0][1]][self._header[self._alias[idx[1]]]]
                        )
                return out
            except:
                try:
                    # return data entry with specified key word
                    out = self._data[idx[0]][self._header[self._alias[idx[1]]]]
                    self._cache[idx] = out
                    return out
                except KeyError:
                    pass

    def get_subset(self, sublist, ref='concept'):
        """
        Function creates a specific subset of all word pairs.

        Parameters
        ----------
        sublist : list
            A list which contains those items which should be considered for
            the subset creation, for example, a list of concepts.
        ref : string (default="concept")
            The reference point to compare the given sublist. 

        Notes
        -----
        This function can be used to consider only a smaller part of word pairs
        when creating a scorer. Normally, all words are compared, but defining
        a subset allows to compare only those belonging to a specific concept
        list (Swadesh list). 
        """
        self.subsets = {}
        for i, tA in enumerate(self.taxa):
            for j, tB in enumerate(self.taxa):
                if i <= j:
                    self.subsets[tA,tB] = []
                    
                    # get current pairs
                    pairs = self.pairs[tA,tB]

                    # iterate over pairs and append those whose reference point
                    # is in the sublist
                    for pair in pairs:
                        if self[pair,ref][0] in sublist:
                            self.subsets[tA,tB] += [pair]
            
    def _get_corrdist(
            self,
            **keywords
            ):
        """
        Use alignments to get a correspondences statistics.
        """
        kw = dict(
                cluster_method          = 'upgma',
                factor                  = rcParams['align_factor'],
                gop                     = rcParams['align_gop'],
                modes                   = rcParams['lexstat_modes'],
                preprocessing           = False,
                preprocessing_method    = rcParams['lexstat_preprocessing_method'],
                preprocessing_threshold = rcParams['lexstat_preprocessing_threshold'],
                ref                     = 'scaid',
                restricted_chars        = rcParams['restricted_chars'],
                threshold               = rcParams['lexstat_threshold'],
                subset                  = False
                )
        kw.update(keywords)

        self._included = {}
        corrdist = {}

        if kw['preprocessing']:
            if kw['ref'] not in self.header:
                self.cluster(
                        method=kw['preprocessing_method'],
                        threshold=kw['preprocessing_threshold'],
                        gop = kw['gop'],
                        cluster_method=kw['cluster_method'],
                        ref=kw['ref']
                        )

        tasks = factorial(len(self.taxa) + 1) / 2 / factorial(len(self.taxa) - 1)
        with util.ProgressBar('CORRESPONDENCE CALCULATION', tasks) as progress:
            for i, j in combinations_with_replacement(range(len(self.taxa)), r=2):
                progress.update()
                tA, tB = self.taxa[i], self.taxa[j]
                self.log.info("Calculating alignments for pair {0} / {1}.".format(tA, tB))

                corrdist[tA,tB] = {}
                for mode,gop,scale in kw['modes']:
                    # XXX this is where we should add the new function for
                    # subsets of swadesh lists XXX
                    # this can be easily done by first checking for a
                    # sublist parameter and then getting all the numbers in
                    # a temporary variable "pairs" for all cases where this
                    # subset is defined, all that needs to be done is to
                    # provide an extra function that creates a
                    # subset-variable or hash in which for all language
                    # pairs the subset is defined.
                    if kw['subset']:
                        pairs = [pair for pair in self.pairs[tA,tB] if \
                                pair in self.subsets[tA,tB]]
                    else:
                        pairs = self.pairs[tA,tB]

                    if kw['preprocessing']:
                        numbers = [self[pair,"numbers"] for pair in pairs \
                                if self[pair, kw['ref']][0] == self[pair,
                                    kw['ref']][1]]
                        weights = [self[pair,"weights"] for pair in pairs \
                                if self[pair, kw['ref']][0] == self[pair,
                                    kw['ref']][1]]
                        prostrings = [self[pair,"prostrings"] for pair in
                                pairs if self[pair, kw['ref']][0] ==  self[pair,
                                    kw['ref']][1]]
                        corrs,included = calign.corrdist(
                                10.0,
                                numbers,
                                weights,
                                prostrings,
                                gop,
                                scale,
                                kw['factor'],
                                self.bscorer,
                                mode,
                                kw['restricted_chars']
                                )
                    else:
                        numbers = [self[pair,"numbers"] for pair in pairs]
                        weights = [self[pair,"weights"] for pair in pairs]
                        prostrings = [self[pair,"prostrings"] for pair in
                                pairs]
                        corrs,included = calign.corrdist(
                                kw['preprocessing_threshold'],
                                numbers,
                                weights,
                                prostrings,
                                gop,
                                scale,
                                kw['factor'],
                                self.bscorer,
                                mode,
                                kw['restricted_chars']
                                )

                    self._included[tA,tB] = included

                    # change representation of gaps
                    for a,b in list(corrs.keys()):
                        # XXX check for bias XXX
                        d = corrs[a,b]
                        if a == '-':
                            a = str(i+1)+'.X.-'
                        elif b == '-':
                            b = str(j+1)+'.X.-'
                        try:
                            corrdist[tA,tB][a,b] += d / len(kw['modes'])
                        except:
                            corrdist[tA,tB][a,b] = d / len(kw['modes'])

        return corrdist

    def _get_randist(
            self,
            **keywords
            ):
        """
        Return the aligned results of randomly aligned sequences.
        """
        kw = dict(
                modes = rcParams['lexstat_modes'],
                factor = rcParams['align_factor'],
                restricted_chars = rcParams['restricted_chars'],
                runs = rcParams['lexstat_runs'],
                rands = rcParams['lexstat_rands'],
                limit = rcParams['lexstat_limit'],
                method = rcParams['lexstat_scoring_method']
                )
        kw.update(keywords)
                
        # determine the mode
        if kw['method'] in ['markov','markov-chain','mc']:
            method = 'markov'
        else:
            method = 'shuffle'

        corrdist = {}
        tasks = factorial(len(self.taxa) + 1) / 2 / factorial(len(self.taxa) - 1)

        if method == 'markov':
            seqs = {}
            pros = {}
            weights = {}

            # get a random distribution for all pairs
            sample = random.sample(
                    [(i,j) for i in range(kw['rands']) for j in range(kw['rands'])],
                    kw['runs']
                    )

            with util.ProgressBar('SEQUENCE GENERATION', len(self.taxa)) as progress:
                for i, taxon in enumerate(self.taxa):
                    progress.update()
                    self.log.info("Analyzing taxon {0}.".format(taxon))

                    tokens = self.get_list(col=taxon, entry="tokens", flat=True)
                    prostrings = self.get_list(col=taxon, entry="prostrings", flat=True)
                    m = MCPhon(tokens,True,prostrings)
                    words = []
                    j = 0
                    k = 0
                    while j < kw['rands']:
                        s = m.get_string(new=False)
                        if s in words:
                            k += 1
                        elif k < kw['limit']:
                            j += 1
                            words += [s]
                        else:
                            j += 1
                            words += [s]

                    seqs[taxon] = []
                    pros[taxon] = []
                    weights[taxon] = []

                    for w in words:
                        cls = tokens2class(w.split(' '),self.model)
                        pros[taxon] += [prosodic_string(w.split(' '))]
                        weights[taxon] += [prosodic_weights(pros[taxon][-1])]
                        seqs[taxon] += [['{0}.{1}'.format(
                            c,
                            p
                            ) for c,p in zip(
                                cls,
                                [self._transform[pr] for pr in pros[taxon][-1]]
                                )
                            ]]

            with util.ProgressBar('RANDOM CORRESPONDENCE CALCULATION', tasks) as progress:
                for i, j in combinations_with_replacement(range(len(self.taxa)), r=2):
                    progress.update()
                    tA, tB = self.taxa[i], self.taxa[j]
                    self.log.info(
                        "Calculating random alignments for pair {0} / {1}.".format(tA, tB))

                    corrdist[tA,tB] = {}
                    for mode,gop,scale in kw['modes']:
                        numbers = [(seqs[tA][x],seqs[tB][y]) for x,y in sample]
                        gops = [(weights[tA][x],weights[tB][y]) for x,y in sample]
                        prostrings = [(pros[tA][x],pros[tB][y]) for x,y in sample]

                        corrs,included = calign.corrdist(
                                10.0,
                                numbers,
                                gops,
                                prostrings,
                                gop,
                                scale,
                                kw['factor'],
                                self.rscorer,
                                mode,
                                kw['restricted_chars']
                                )

                        # change representation of gaps
                        for a,b in list(corrs.keys()):
                            # get the correspondence count
                            d = corrs[a,b] * self._included[tA,tB] / included # XXX check XXX * len(self.pairs[tA,tB]) / runs

                            # check for gaps
                            if a == '-':
                                a = 'X.-'
                            elif b == '-':
                                b = 'X.-'

                            a = str(i+1)+'.'+a
                            b = str(j+1)+'.'+b

                            # append to overall dist
                            try:
                                corrdist[tA,tB][a,b] += d / len(kw['modes'])
                            except:
                                corrdist[tA,tB][a,b] = d / len(kw['modes'])

        # use shuffle approach otherwise
        else:
            tasks = factorial(len(self.taxa) + 1) / 2 / factorial(len(self.taxa) - 1)
            with util.ProgressBar('RANDOM CORRESPONDENCE CALCULATION', tasks) as progress:
                for i, j in combinations_with_replacement(range(len(self.taxa)), r=2):
                    progress.update()
                    tA, tB = self.taxa[i], self.taxa[j]
                    self.log.info(
                        "Calculating random alignments for pair {0} / {1}.".format(tA, tB))

                    corrdist[tA,tB] = {}

                    # get the number pairs etc.
                    numbers = [self[pair,'numbers'] for pair in
                            self.pairs[tA,tB]]
                    gops = [self[pair,'weights'] for pair in
                            self.pairs[tA,tB]]
                    prostrings = [self[pair,'prostrings'] for pair in
                            self.pairs[tA,tB]]

                    try:
                        sample = random.sample(
                                [(x,y) for x in range(len(numbers)) for y in
                                    range(len(numbers))],
                                kw['runs']
                                )
                    # handle exception of sample is larger than population
                    except ValueError:
                        sample = [(x,y) for x in range(len(numbers)) for y
                                in range(len(numbers))]

                    # get an index that will be repeatedly changed
                    #indices = list(range(len(numbers)))

                    for mode,gop,scale in kw['modes']:
                        nnums = [(numbers[s[0]][0],numbers[s[1]][1]) for
                                s in sample]
                        ggops = [(gops[s[0]][0],gops[s[1]][1]) for s in
                                sample]
                        ppros = [(prostrings[s[0]][0],prostrings[s[1]][1]) for s in
                                sample]

                        corrs,included = calign.corrdist(
                                10.0,
                                nnums,
                                ggops,
                                ppros,
                                gop,
                                scale,
                                kw['factor'],
                                self.bscorer,
                                mode,
                                kw['restricted_chars']
                                )

                        # change representation of gaps
                        for a,b in list(corrs.keys()):

                            # get the correspondence count
                            d = corrs[a,b] * self._included[tA,tB] / included #XXX check XXX* len(self.pairs[tA,tB]) / runs

                            # check for gaps
                            if a == '-':
                                a = str(i+1)+'.X.-'

                            elif b == '-':
                                b = str(j+1)+'.X.-'

                            # append to overall dist
                            try:
                                corrdist[tA,tB][a,b] += d / len(kw['modes'])
                            except:
                                corrdist[tA,tB][a,b] = d / len(kw['modes'])

        return corrdist

    def get_scorer(
            self,
            **keywords
            ):
        """
        Create a scoring function based on sound correspondences.

        Parameters
        ----------
        method : str (default='markov')
            Select between "markov", for automatically generated random
            strings, and "shuffle", for random strings taken directly from the
            data.
        ratio : tuple (default=3,2)
            Define the ratio between derived and original score for
            sound-matches.
        vscale : float (default=0.5)
            Define a scaling factor for vowels, in order to decrease their
            score in the calculations.
        runs : int (default=1000)
            Choose the number of random runs that shall be made in order to
            derive the random distribution.
        threshold : float (default=0.7)
            The threshold which used to select those words that are compared in
            order to derive the attested distribution. 
        modes : list (default = [("global",-2,0.5),("local",-1,0.5)])
            The modes which are used in order to derive the distributions from
            pairwise alignments.
        factor : float (default=0.3)
            The scaling factor for sound segments with identical prosodic
            environment.
        force : bool (default=False)
            Force recalculation of existing distribution.
        preprocessing: bool (default=False)
            Select whether SCA-analysis shall be used to derive a preliminary
            set of cognates from which the attested distribution shall be
            derived.
        rands : int (default=1000)
            If "method" is set to "markov", this parameter defines the number
            of strings to produce for the calculation of the random
            distribution.
        limit : int (default=10000)
            If "method" is set to "markov", this parameter defines the limit
            above which no more search for unique strings will be carried out.
        cluster_method : {"upgma" "single" "complete"} (default="upgma")
            Select the method to be used for the calculation of cognates in the
            preprocessing phase, if "preprocessing" is set to c{True}.
        gop : int (default=-2)
            If "preprocessing" is selected, define the gap opening penalty for
            the preprocessing calculation of cognates.
        """
        kw = dict(
            method                  = rcParams['lexstat_scoring_method'],
            ratio                   = rcParams['lexstat_ratio'],
            vscale                  = rcParams['lexstat_vscale'],
            runs                    = rcParams['lexstat_runs'],
            #threshold               = rcParams['lexstat_threshold'],
            modes                   = rcParams['lexstat_modes'],
            factor                  = rcParams['align_factor'],
            restricted_chars        = rcParams['restricted_chars'],
            force                   = False,
            preprocessing           = True,
            rands                   = rcParams['lexstat_rands'],
            limit                   = rcParams['lexstat_limit'],
            cluster_method          = rcParams['lexstat_cluster_method'],
            gop                     = rcParams['align_gop'],
            preprocessing_threshold = rcParams['lexstat_preprocessing_threshold'],
            preprocessing_method    = rcParams['lexstat_preprocessing_method'],
            subset                  = False,
            defaults                = False,
            )
        kw.update(keywords)
        if kw['defaults']:
            return kw

        # get parameters and store them in string
        modestring = []
        for a,b,c in kw['modes']:
            modestring += ['{0}-{1}-{2:.2f}'.format(a,abs(b),c)]
        modestring = ':'.join(modestring)
        
        params = dict(
                ratio = kw['ratio'],
                vscale = kw['vscale'],
                runs = kw['runs'],
                threshold = kw['preprocessing_threshold'],
                modestring = modestring,
                factor = kw['factor'],
                restricted_chars = kw['restricted_chars'],
                method = kw['method'],
                preprocessing = '{0}:{1}:{2}'.format(
                    kw['preprocessing'],
                    kw['cluster_method'],
                    kw['gop']
                    )
                )

        parstring = '_'.join(
                [
                    '{ratio[0]}:{ratio[1]}'
                    '{vscale:.2f}',
                    '{runs}',
                    '{threshold:.2f}',
                    '{modestring}',
                    '{factor:.2f}',
                    '{restricted_chars}',
                    '{method}',
                    '{preprocessing}'
                    ]).format(
                **params
                )

        # check for existing attributes
        if hasattr(self,'cscorer') and not kw['force']:
            self.log.warn(
                "An identical scoring function has already been calculated, force "
                "recalculation by setting 'force' to 'True'.")
            return

        # check for attribute
        if hasattr(self,'params') and not kw['force']:
            if 'cscorer' in self.params:
                if self.params['cscorer'] == params:
                    self.log.warn(
                        "An identical scoring function has already been calculated, force "
                        "recalculation by setting 'force' to 'True'.")
                    return
            else:
                self.log.warn(
                    "A different scoring function has already been calculated, overwriting previous settings.")

        # store parameters
        self.params = {'cscorer':params }
        self._meta['params'] = self.params
        self._stamp += "# Parameters: "+parstring+'\n'

        # get the correspondence distribution
        corrdist = self._get_corrdist(**kw)

        # get the random distribution
        randist = self._get_randist(**kw)
        
        # store the distributions as attributes
        self._corrdist = corrdist
        self._randist = randist
        
        # get the average gop
        gop = sum([m[1] for m in kw['modes']]) / len(kw['modes'])

        # create the new scoring matrix
        matrix = [[c for c in line] for line in self.bscorer.matrix]
        char_dict = self.bscorer.chars2int

        # start the calculation
        for i,tA in enumerate(self.taxa):
            for j,tB in enumerate(self.taxa):
                if i <= j:
                    for charA in list(self.freqs[tA]) + [str(i+1)+'.X.-']:
                        for charB in list(self.freqs[tB]) + [str(j+1)+'.X.-']:
                            try:
                                exp = randist[tA,tB][charA,charB]
                            except:
                                exp = False
                            try:
                                att = corrdist[tA,tB][charA,charB]
                            except:
                                att = False

                            # in the following we follow the former lexstat
                            # protocol
                            if att <= 1 and i != j:
                                att = False

                            if att and exp:
                                score = np.log2((att ** 2 ) / ( exp ** 2 ) )
                            elif att and not exp:
                                score = np.log2((att ** 2 ) / 0.00001 )
                            elif exp and not att:
                                score = -5  #XXX gop ??? 
                            elif not exp and not att:
                                score = -90 # ???

                            # combine the scores
                            if '-' not in charA+charB:
                                sim = self.bscorer[charA,charB]
                            else:
                                sim = gop

                            # get the real score
                            rscore = ( kw['ratio'][0] * score + kw['ratio'][1] * sim ) / sum (kw['ratio'])
                            
                            try:
                                idxA = char_dict[charA]
                                idxB = char_dict[charB]

                                # use the vowel scale
                                if charA[4] in 'XYZT_' and charB[4] in 'XYZT_':
                                    matrix[idxA][idxB] = kw['vscale'] * rscore
                                    matrix[idxB][idxA] = kw['vscale'] * rscore
                                else:
                                    matrix[idxA][idxB] = rscore
                                    matrix[idxB][idxA] = rscore
                            except:
                                pass
        
        self.cscorer = misc.ScoreDict(self.chars,matrix)
        self._meta['scorer']['cscorer'] = self.cscorer

    def align_pairs(
            self,
            idxA,
            idxB,
            concept = None,
            **keywords
            ):
        """
        Align all or some words of a given pair of languages.

        Parameters
        ----------
        idxA,idxB : {int, str}
            Use an integer to refer to the words by their unique internal ID,
            use language names to select all words for a given language.
        method : {'lexstat','sca'}
            Define the method to be used for the alignment of the words.
        mode : {'global','local','overlap','dialign'} (default='overlap')
            Select the mode for the alignment analysis.
        gop : int (default=-2)
            If 'sca' is selected as a method, define the gap opening penalty.
        scale : float (default=0.5)
            Select the scale for the gap extension penalty.
        factor : float (default=0.3)
            Select the factor for extra scores for identical prosodic segments.
        restricted_chars : str (default="T_")
            Select the restricted chars (boundary markers) in the prosodic
            strings in order to enable secondary alignment.
        distance : bool (default=True)
            If set to c{True}, return the distance instead of the similarity
            score.
        pprint : bool (default=True)
            If set to c{True}, print the results to the terminal.
        return_distance : bool (default=False)
            If set to c{True}, return the distance score, otherwise, nothing
            will be returned.
        """
        kw = dict(
                method           = 'lexstat',
                mode             = "global",
                scale            = 0.5,
                factor           = 0.3,
                restricted_chars = '_T',
                pprint           = True,
                return_distance  = False,
                gop              = -2,
                distance         = True,
                defaults         = False,
                return_raw       = False
                )
        kw.update(keywords)
        if kw['defaults']: return kw
        
        if isinstance(idxA, (text_type, tuple)):
            if isinstance(idxA, tuple):
                idxsA = self.get_dict(col=idxA[0])[idxA[1]]
                idxsB = self.get_dict(col=idxB[0])[idxB[1]]
                for i,indexA in enumerate(idxsA):
                    for j,indexB in enumerate(idxsB):
                        self.align_pairs(indexA,indexB,**kw)

            else:
                if not concept:
                    for c in self.concepts:
                        print("Concept: {0}".format(c))
                        concept = c
                        self.align_pairs(idxA,idxB,c,**kw)
                        print('')
                else:
                    self.align_pairs(
                            (idxA,concept),
                            (idxB,concept),
                            concept=None,
                            **kw
                            )
            return
        
        # assign the distance value
        distance = 1 if kw['distance'] else 0

        # get the language ids
        lA = self[idxA,'langid']
        lB = self[idxB,'langid']

        if kw['method'] == 'lexstat':
            scorer = self.cscorer
            gop = 1.0
            weightsA = [self.cscorer[str(lA)+'.X.-',n] for n in
                self[idxA,'numbers']]
            weightsB = [self.cscorer[str(lB)+'.X.-',n] for n in
                self[idxB,'numbers']]

        else:
            gop = kw['gop']
            weightsA = self[idxA,'weights']
            weightsB = self[idxB,'weights']
            scorer = self.bscorer

        almA,almB,d = calign.align_pair(
                self[idxA,'numbers'],
                self[idxB,'numbers'],
                weightsA,
                weightsB,
                self[idxA,'prostrings'],
                self[idxB,'prostrings'],
                gop,
                kw['scale'],
                kw['factor'],
                scorer,
                kw['mode'],
                kw['restricted_chars'],
                distance
                )

        # get a string of scores
        if kw['method'] == 'lexstat':
            fun = lambda x,y: x if x != '-' else '{0}.X.-'.format(y)

            scoreA = [fun(a,lA) for a in almA]
            scoreB = [fun(b,lB) for b in almB]
        else:
            scoreA = almA
            scoreB = almB

        scores = ['{0:.2f}'.format(scorer[a,b]) for a,b in zip(scoreA,scoreB)]
        
        if kw['return_raw']:
            return almA, almB, d
        
        almA = class2tokens(self[idxA,'tokens'],almA)
        almB = class2tokens(self[idxB,'tokens'],almB)
        if kw['pprint']:
            print('\t'.join(almA))
            print('\t'.join(almB))
            print('\t'.join(scores))
            if kw['distance']:
                print('Distance: {0:.2f}'.format(d))
            else:
                print('Similarity: {0:.2f}'.format(d))
        
        if kw['return_distance']:
            return d
        return almA,almB,d
    
    def _get_matrices(
            self,
            concept = False,
            method = 'sca',
            scale = 0.5,
            factor = 0.3,
            restricted_chars = '_T',
            mode = 'overlap',
            gop = -2,
            restriction = '',
            **keywords
            ):
        """
        Calculate alignment matrices.

        Notes
        -----
        This is an iterator object and it yields the indices of a given
        concept, the matrix, and the concept.
        """
        # currently, there are no defaults XXX
        kw = dict(
                defaults = False,
                external_scorer = False, # external scoring function
                )
        kw.update(keywords)

        # check for method
        if method == 'lexstat':
            
            # check for scorer
            if not hasattr(self,'cscorer'):
                self.log.warn("No correspondence-scorer has been specified.")
                return
            
            # define the function with help of lambda
            function = lambda idxA,idxy: calign.align_pair(
                    self[idxA,'numbers'],
                    self[idxB,'numbers'],
                    [self.cscorer[self[idxB,'langid'] + ".X.-",n] for n in
                        self[idxA,'numbers']],
                    [self.cscorer[self[idxA,'langid'] + ".X.-",n] for n in
                        self[idxB,'numbers']],

                    self[idxA,'prostrings'],
                    self[idxB,'prostrings'],
                    1,
                    scale,
                    factor,
                    self.cscorer,
                    mode,
                    restricted_chars,
                    1
                    )[2]
        elif method == 'sca':
            # define the function with help of lambda
            function = lambda idxA,idxB: calign.align_pair(
                    self[idxA,'numbers'],
                    self[idxB,'numbers'],
                    self[idxA,'weights'],
                    self[idxB,'weights'],
                    self[idxA,'prostrings'],
                    self[idxB,'prostrings'],
                    gop,
                    scale,
                    factor,
                    self.bscorer,
                    mode,
                    restricted_chars,
                    1
                    )[2]  

        elif method == 'edit-dist':
            try:
                entry = kw['entry']
            except:
                entry = 'tokens'

            # define function with lamda
            function = lambda idxA,idxB: edit_dist(
                    self[idxA,entry],
                    self[idxB,entry],
                    True,
                    restriction
                    )

        elif method == 'turchin':
            function = lambda idxA,idxB: turchin(
                    self[idxA,'tokens'],
                    self[idxB,'tokens']
                    )

        elif method == 'custom':
            
            function = lambda idxA,idxB: talign.align_pair(
                    self[idxA, 'utokens'],
                    self[idxB, 'utokens'],
                    gop,
                    scale,
                    keywords['external_scorer'],
                    'overlap',
                    True)[2]

        if not concept:
            concepts = sorted(self.rows)
        else:
            concepts = [concept]

        for c in sorted(concepts):
            self.log.info("Analyzing words for concept <{0}>.".format(c))

            indices = self.get_list(
                    row=c,
                    flat=True
                    )

            matrix = [] #matrices[concept]
            
            for i,idxA in enumerate(indices):
                for j,idxB in enumerate(indices):
                    if i < j:
                        d = function(idxA,idxB)
                        
                        # append distance score to matrix
                        matrix += [d]
            
            # squareform the matrix 
            matrix = misc.squareform(matrix)
            
            if not concept:
                yield c,indices,matrix
            else:
                yield matrix

    def cluster(
            self,
            method = 'sca',
            cluster_method='upgma',
            threshold = 0.3,
            scale = 0.5,
            factor = 0.3,
            restricted_chars = '_T',
            mode = 'overlap',
            gop = -2,
            restriction = '',
            ref = '',
            external_function = None,
            **keywords
            ):
        """
        Function for flat clustering of words into cognate sets.

        Parameters
        ----------
        method : {'sca','lexstat','edit-dist','turchin'} (default='sca')
            Select the method that shall be used for the calculation.
        cluster_method : {'upgma','single','complete', 'mcl'} (default='upgma')
            Select the cluster method. 'upgma' (:evobib:`Sokal1958`) refers to
            average linkage clustering, 'mcl' refers to the "Markov Clustering
            Algorithm" (:evobib:`Dongen2000`).
        threshold : float (default=0.3)
            Select the threshold for the cluster approach. If set to c{False},
            an automatic threshold will be calculated by calculating the
            average distance of unrelated sequences (use with care).
        scale : float (default=0.5)
            Select the scale for the gap extension penalty.
        factor : float (default=0.3)
            Select the factor for extra scores for identical prosodic segments.
        restricted_chars : str (default="T_")
            Select the restricted chars (boundary markers) in the prosodic
            strings in order to enable secondary alignment.
        mode : {'global','local','overlap','dialign'} (default='overlap')
            Select the mode for the alignment analysis.
        verbose : bool (default=False)
            Define whether verbose output should be used or not.
        gop : int (default=-2)
            If 'sca' is selected as a method, define the gap opening penalty.
        restriction : {'cv'} (default="")
            Specify the restriction for calculations using the edit-distance.
            Currently, only "cv" is supported. If *edit-dist* is selected as
            *method* and *restriction* is set to *cv*, consonant-vowel matches
            will be prohibited in the calculations and the edit distance will
            be normalized by the length of the alignment rather than the length
            of the longest sequence, as described in :evobib:`Heeringa2006`.
        inflation : {int, float} (default=2)
            Specify the inflation parameter for the use of the MCL algorithm.
        expansion : int (default=2)
            Specify the expansion parameter for the use of the MCL algorithm.

        """
        # set up defaults
        kw = dict(
                inflation       = 2,
                expansion       = 2,
                max_steps       = 1000,
                add_self_loops  = True,
                guess_threshold = False,
                gt_trange       = (0.4,0.6,0.02),
                mcl_logs        = lambda x: -np.log2((1-x)**2),
                gt_mode         = 'average',
                matrix_type     = 'distances',
                link_threshold  = False,
                _return_matrix  = False, # help function for test purposes
                defaults        = False,
                external_scorer = False, # external scoring dictionary
                )
        kw.update(keywords)
        if kw['defaults']: return kw
        
        # check for parameters and add clustering, in order to make sure that
        # analyses are not repeated
        if hasattr(self,'params'):
            pass
        else:
            self.params = {}
        
        self.params['cluster'] = "{0}_{1}_{2:.2f}".format(
                method,
                cluster_method,
                threshold
                )
        self._stamp += '# Cluster: ' + self.params['cluster']
        
        if method not in ['lexstat','sca','turchin','edit-dist', 'custom']:
            raise ValueError(
                    "[!] The method you selected is not available."
                    )
        
        # set up clustering algorithm, first the simple basics
        if external_function:
            fclust = external_function

        elif cluster_method in ['upgma','single','complete']:
            fclust = lambda x,y: clustering.flat_cluster(
                    cluster_method,
                    y,
                    x,
                    revert = True
                    )
        # we need specific conditions for mcl clustering
        elif cluster_method == 'mcl':
            fclust = lambda x,y: clustering.mcl(
                    y,
                    x,
                    list(range(len(x))),
                    max_steps = kw['max_steps'],
                    inflation = kw['inflation'],
                    expansion = kw['expansion'],
                    add_self_loops = kw['add_self_loops'],
                    logs = kw['mcl_logs'],
                    revert = True,
                    )
        elif cluster_method in ['lcl','link_clustering','lc']:
            fclust = lambda x,y: clustering.link_clustering(
                    y,
                    x,
                    list(range(len(x))),
                    revert = True,
                    fuzzy = False,
                    matrix_type = kw['matrix_type'],
                    link_threshold = kw['link_threshold']
                    )

        # make a dictionary that stores the clusters for later update
        clr = {}
        k = 0
        
        # create a matrix iterator
        matrices = self._get_matrices(
                method            = method,
                scale             = scale,
                factor            = factor,
                restricted_chars  = restricted_chars,
                mode              = mode,
                gop               = gop,
                restriction       = restriction,
                **kw
                )

        # check for full consideration of basic t
        if kw['guess_threshold'] and kw['gt_mode'] == 'average':
            thresholds = []
            matrices = list(matrices)
            for c,i,m in matrices:
                t = clustering.best_threshold(
                    m,
                    kw['gt_trange']
                    )
                thresholds += [t]
            threshold = sum(thresholds) / len(thresholds)
        # new method for threshold estimation based on calculating approximate
        # random distributions of similarities for each sequence
        elif kw['guess_threshold'] and kw['gt_mode'] == 'nulld':
            DR = []
            align = lambda x,y: self.align_pairs(x, y, method=method,
                    restricted_chars=restricted_chars, mode=mode, scale=scale,
                    factor=factor, return_distance=True, pprint=False, gop=gop)
            for l1,l2 in self.pairs:
                if l1 != l2:
                    pairs = self.pairs[l1,l2]
                    for p1,p2 in pairs:
                        dx = [align(p1, pairs[random.randint(0, len(pairs)-1)][1])
                                for i in range(len(pairs)//5)]
                        DR += dx #[sum(dx)/len(dx)]
            threshold = sum(DR) / len(DR)

        with util.ProgressBar('SEQUENCE CLUSTERING', len(self.rows)) as progress:
            for concept,indices,matrix in matrices:
                progress.update()

                # check for keyword to guess the threshold
                if kw['guess_threshold'] and kw['gt_mode'] == 'item':
                    t = clustering.best_threshold(
                        matrix,
                        kw['gt_trange']
                        )
                # considering new function here JML
                elif kw['guess_threshold'] and kw['gt_mode'] == 'nullditem':
                    for idx in indices:
                        pass
                else:
                    t = threshold

                c = fclust(matrix,t)
            
                # specific clustering for fuzzy methods, currently not yet
                # supported
                if cluster_method in ['fuzzy']: #['link_communities','lc','lcl']:
                    clusters = [[d+k for d in c[i]] for i in range(len(matrix))]
                    tests = []
                    for clrx in clusters:
                        for x in clrx:
                            tests += [x]
                    k = max(tests)
                    for idxA,idxB in zip(indices,clusters):
                        clr[idxA] = idxB
                    
                else:
                    # extract the clusters
                    clusters = [c[i]+k for i in range(len(matrix))]

                    # reassign the "k" value
                    k = max(clusters)
            
                    # add values to cluster dictionary
                    for idxA,idxB in zip(indices,clusters):
                        clr[idxA] = idxB
        
        if 'override' in kw:
            override = kw['override']
        else:
            override = False

        # assign ids
        if not ref:
            if method == 'turchin':
                self.add_entries('turchinid',clr,lambda x:x,override=override)
            elif method == 'lexstat':
                self.add_entries('lexstatid',clr,lambda x:x,override=override)
            elif method == 'sca':
                self.add_entries('scaid',clr,lambda x:x,override=override)
            elif method == 'custom':
                self.add_entries('customid',clr, lambda x:x, override=override)
            else:
                self.add_entries('editid',clr,lambda x:x,override=override)       
        else:
            self.add_entries(ref,clr,lambda x:x,override=override)

        # assign thresholds to parameters
        self._current_threshold = threshold


    def get_random_distances(
            self,
            method='lexstat',
            runs = 100,
            mode = 'overlap',
            gop = -2,
            scale = 0.5,
            factor = 0.3,
            restricted_chars = 'T_'
            ):
        """
        Method calculates randoms scores for unrelated words in a dataset.

        Parameters
        ----------
        method : {'sca','lexstat','edit-dist','turchin'} (default='sca')
            Select the method that shall be used for the calculation.
        runs : int (default=100)
            Select the number of random alignments for each language pair.
        mode : {'global','local','overlap','dialign'} (default='overlap')
            Select the mode for the alignment analysis.
        gop : int (default=-2)
            If 'sca' is selected as a method, define the gap opening penalty.
        scale : float (default=0.5)
            Select the scale for the gap extension penalty.
        factor : float (default=0.3)
            Select the factor for extra scores for identical prosodic segments.
        restricted_chars : str (default="T_")
            Select the restricted chars (boundary markers) in the prosodic
            strings in order to enable secondary alignment.

        Returns
        -------
        D : c{numpy.array}
            An array with all distances calculated for each sequence pair.
        """
        D = []
        
        if method in ['sca','lexstat']:
            function = lambda x,y: self.align_pairs(
                    x,
                    y,
                    method=method,
                    distance=True,
                    return_distance=True,
                    pprint=False,
                    mode = mode,
                    scale = scale,
                    factor = factor,
                    gop = gop
                    )
        else:
            function = lambda x,y: edit_dist(
                    self[x,'tokens'],
                    self[y,'tokens']
                    )

        for i,taxA in enumerate(self.taxa):
            for j,taxB in enumerate(self.taxa):
                if i < j:

                    # get a random selection of words from both taxa
                    pairs = self.pairs[taxA,taxB]
                    
                    try:
                        sample = random.sample(
                                [(x,y) for x in range(len(pairs)) for y in
                                    range(len(pairs))],
                                runs
                                )
                    except ValueError:
                        sample = random.sample(
                                [(x,y) for x in range(len(pairs)) for y in
                                    range(len(pairs))],
                                len(pairs)
                                )

                    sample_pairs = [(pairs[x][0],pairs[y][1]) for x,y in sample]
                    for pA,pB in sample_pairs:
                        d = function(pA,pB)

                        D += [d]

        return sorted(D)
    
    def get_distances(
            self,
            method='sca',
            mode = 'overlap',
            gop = -2,
            scale = 0.5,
            factor = 0.3,
            restricted_chars = 'T_',
            aggregate = True
            ):
        """
        Method calculates different distance estimates for language pairs.

        Parameters
        ----------
        method : {'sca','lexstat','edit-dist','turchin'} (default='sca')
            Select the method that shall be used for the calculation.
        runs : int (default=100)
            Select the number of random alignments for each language pair.
        mode : {'global','local','overlap','dialign'} (default='overlap')
            Select the mode for the alignment analysis.
        gop : int (default=-2)
            If 'sca' is selected as a method, define the gap opening penalty.
        scale : float (default=0.5)
            Select the scale for the gap extension penalty.
        factor : float (default=0.3)
            Select the factor for extra scores for identical prosodic segments.
        restricted_chars : str (default="T_")
            Select the restricted chars (boundary markers) in the prosodic
            strings in order to enable secondary alignment.
        aggregate : bool (default=True)
            Return aggregated distances in form of a distance matrix for all
            taxa in the data.

        Returns
        -------
        D : c{numpy.array}
            An array with all distances calculated for each sequence pair.
        """
        D = []
        
        if method in ['sca','lexstat']:
            function = lambda x,y: self.align_pairs(
                    x,
                    y,
                    method=method,
                    distance=True,
                    return_distance=True,
                    pprint=False,
                    mode = mode,
                    scale = scale,
                    factor = factor,
                    gop = gop
                    )
        else:
            function = lambda x,y: edit_dist(
                    self[x,'tokens'],
                    self[y,'tokens'],
                    normalized=True
                    )
        if not aggregate:
            for i,taxA in enumerate(self.taxa):
                for j,taxB in enumerate(self.taxa):
                    if i < j:

                        # get a random selection of words from both taxa
                        sample_pairs = self.pairs[taxA,taxB]
                        
                        for pA,pB in sample_pairs:
                            d = function(pA,pB)

                            D += [d]
            D = sorted(D)
        else:
            for i,taxA in enumerate(self.taxa):
                for j,taxB in enumerate(self.taxa):
                    if i < j:
                        sample_pairs = self.pairs[taxA,taxB]
                        
                        distances = []
                        for pA,pB in sample_pairs:
                            try:
                                d = function(pA,pB)
                            except:
                                self.log.error("Zero-Warning")
                                d = 1.0
                            distances += [d]
                        D += [sum(distances) / len(distances)]
            D = misc.squareform(D)

        return D

    def output(
            self,
            fileformat,
            **keywords
            ):
        """
        Write data for lexstat to file.

        Parameters
        ----------
        fileformat : {'csv', 'tre','nwk','dst', 'taxa', 'starling', 'paps.nex', 'paps.csv'}
            The format that is written to file. This corresponds to the file
            extension, thus 'csv' creates a file in csv-format, 'dst' creates
            a file in Phylip-distance format, etc.
        filename : str
            Specify the name of the output file (defaults to a filename that
            indicates the creation date).
        subset : bool (default=False)
            If set to c{True}, return only a subset of the data. Which subset
            is specified in the keywords 'cols' and 'rows'.
        cols : list
            If *subset* is set to c{True}, specify the columns that shall be
            written to the csv-file.
        rows : dict
            If *subset* is set to c{True}, use a dictionary consisting of keys
            that specify a column and values that give a Python-statement in
            raw text, such as, e.g., "== 'hand'". The content of the specified
            column will then be checked against statement passed in the
            dictionary, and if it is evaluated to c{True}, the respective row
            will be written to file.
        cognates : str
            Name of the column that contains the cognate IDs if 'starling' is
            chosen as an output format.

        missing : { str, int } (default=0)
            If 'paps.nex' or 'paps.csv' is chosen as fileformat, this character
            will be inserted as an indicator of missing data.

        tree_calc : {'neighbor', 'upgma'}
            If no tree has been calculated and 'tre' or 'nwk' is chosen as
            output format, the method that is used to calculate the tree.

        threshold : float (default=0.6)
            The threshold that is used to carry out a flat cluster analysis if
            'groups' or 'cluster' is chosen as output format.
        """

        kw = dict(
                filename = self.filename,
                defaults = False
                )
        kw.update(keywords)
        if kw['defaults']: return kw

        if fileformat == 'scorer':
            if 'scorer' not in kw:
                kw['scorer'] = self.rscorer
            out = scorer2str(kw['scorer'])
            util.write_text_file(kw['filename'] + '.' + fileformat, out)
        else:
            self._output(fileformat,**kw)
