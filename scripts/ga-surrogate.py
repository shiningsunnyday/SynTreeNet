from ga.config import *
from ga.search import *
from ga.utils import *
from synnet.utils.analysis_utils import serialize_string
from synnet.utils.reconstruct_utils import *
import pickle
import logging
from tdc import Oracle

logger = logging.getLogger(__name__)

def get_args():
    import argparse

    parser = argparse.ArgumentParser()
    # File I/O
    parser.add_argument(
        "--building-blocks-file",
        type=str,
        default="data/assets/building-blocks/enamine_us_matched.csv",  # TODO: change
        help="Input file with SMILES strings (First row `SMILES`, then one per line).",
    )
    parser.add_argument(
        "--rxn-templates-file",
        type=str,
        default="data/assets/reaction-templates/hb.txt",  # TODO: change
        help="Input file with reaction templates as SMARTS(No header, one per line).",
    )
    parser.add_argument(
        "--rxns_collection_file",
        type=str,
        default="data/assets/reaction-templates/reactions_hb.json.gz",
    )
    parser.add_argument(
        "--embeddings-knn-file",
        type=str,
        help="Input file for the pre-computed embeddings (*.npy).",
        default="data/assets/building-blocks/enamine_us_emb_fp_256.npy"
    )    
    parser.add_argument("--ckpt-bb", type=str, help="Model checkpoint to use")
    parser.add_argument("--ckpt-rxn", type=str, help="Model checkpoint to use")    
    parser.add_argument("--ckpt-dir", type=str, help="Model checkpoint dir, if given assume one ckpt per class")    
    parser.add_argument(
        "--hash-dir",
        default="",
        required=True
    )    
    parser.add_argument(
        "--skeleton-file",
        type=str,
        default="results/viz/top_1000/skeletons-top-1000.pkl",
        help="Input file for the skeletons of syntree-file",
    )   
    parser.add_argument("--forcing-eval", action='store_true')
    parser.add_argument("--mermaid", action='store_true')
    parser.add_argument("--top-k", default=1, type=int)
    parser.add_argument("--filter-only", type=str, nargs='+', choices=['rxn', 'bb'], default=[])    
    parser.add_argument("--log_file")
    # Processing
    parser.add_argument("--ncpu", type=int, help="Number of cpus")
    # Evaluation
    parser.add_argument(
        "--objective", type=str, default="qed", help="Objective function to optimize"
    )    
    return parser.parse_args()  


def test_fitness(batch):
    for ind in batch:
        fp = ind.fp
        bt = ind.bt
        leaves = [v for v in bt.nodes() if (bt.out_degree(v) == 0)]
        ind.fitness = 0.05 * (fp[:100].sum() - fp[100:].sum()) + len(leaves)



def test_surrogate(batch):
    for ind in batch:
        fp = ind.fp
        bt = ind.bt        
        sk = binary_tree_to_skeleton(bt)        
        ind.fitness = surrogate(sk, fp, oracle)


def set_oracle(args):
    obj = args.objective
    if obj == "qed":
        # define the oracle function from the TDC
        return Oracle(name="QED")        
    elif obj == "logp":
        # define the oracle function from the TDC
        return Oracle(name="LogP")        
    elif obj == "jnk":
        # return oracle function from the TDC
        return Oracle(name="JNK3")        
    elif obj == "gsk":
        # return oracle function from the TDC
        return Oracle(name="GSK3B")        
    elif obj == "drd2":
        # return oracle function from the TDC
        return Oracle(name="DRD2")        
    elif obj == "7l11":
        return dock_7l11
    elif obj == "drd3":
        return dock_drd3
    else:
        raise ValueError("Objective function not implemneted")    



def init_global_vars(args):
    skeletons = pickle.load(open(args.skeleton_file, 'rb'))    
    set_models(args, logger)
    load_data(args, logger)
    oracle = set_oracle(args)
    globals()['oracle'] = oracle



def main(args):
    if args.log_file:
        handler = logging.FileHandler(args.log_file)
    logger.addHandler(handler)    
    init_global_vars(args)    
    config = GeneticSearchConfig(wandb=True)
    GeneticSearch(config).optimize(test_surrogate)



if __name__ == "__main__":
    args = get_args()
    breakpoint()
    main(args)
    