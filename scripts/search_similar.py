"""
Computes the fingerprint similarity of molecules in the validation and test set to
molecules in the training set.
"""
import numpy as np
import pandas as pd
from syn_net.utils.data_utils import *
from rdkit import Chem
from rdkit.Chem import AllChem
import multiprocessing as mp
from rdkit import DataStructs

def func(fp: np.ndarray, fps_reference: np.ndarray):
    """Finds most similar fingerprint in a reference set for `fp`.
    Uses Tanimoto Similarity.
    """
    dists = np.array(
        [DataStructs.FingerprintSimilarity(fp, fp_, metric=DataStructs.TanimotoSimilarity) for fp_ in fps_train])
    similarity_score, idx = dists.max(), dists.argmax()
    return similarity_score, idx

def _compute_fp_bitvector(smiles: list[str], radius: int=2, nbits: int=1024):
     return [AllChem.GetMorganFingerprintAsBitVect(Chem.MolFromSmiles(smi), radius, nBits=nbits) for smi in smiles]

def _save_df(file: str, df):
    if file is None: return
    df.to_csv(file, index=False)

if __name__ == '__main__':

    ncpu = 64

    file = '/pool001/whgao/data/synth_net/st_hb/st_train.json.gz'
    syntree_collection = SyntheticTreeSet().load(file)
    data_train = [st.root.smiles for st in syntree_collection]
    fps_train = _compute_fp_bitvector(data_train)

    file = '/pool001/whgao/data/synth_net/st_hb/st_test.json.gz'
    syntree_collection = SyntheticTreeSet().load(file)
    data_test = [st.root.smiles for st in syntree_collection]
    fps_test = _compute_fp_bitvector(data_test)

    file = '/pool001/whgao/data/synth_net/st_hb/st_valid.json.gz'
    syntree_collection = SyntheticTreeSet().load(file)
    data_valid = [st.root.smiles for st in syntree_collection]
    fps_valid = _compute_fp_bitvector(data_valid)

    with mp.Pool(processes=ncpu) as pool:
        results = pool.map(func, fps_valid)

    similaritys = [r[0] for r in results]
    indices = [data_train[r[1]] for r in results]
    df1 = pd.DataFrame({'smiles': data_valid, 'split': 'valid', 'most similar': indices, 'similarity': similaritys})

    with mp.Pool(processes=ncpu) as pool:
        results = pool.map(func, fps_test)

    similaritys = [r[0] for r in results]
    indices = [data_train[r[1]] for r in results]
    df2 = pd.DataFrame({'smiles': data_test, 'split': 'test', 'most similar': indices, 'similarity': similaritys})

    outfile = 'data_similarity.csv'
    _save_df(outfile,  pd.concat([df1, df2], axis=0, ignore_index=True))


    print('Finish!')
