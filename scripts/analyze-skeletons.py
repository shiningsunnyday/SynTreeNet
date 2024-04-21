from synnet.utils.data_utils import SyntheticTree, SyntheticTreeSet, Skeleton, ReactionSet
from synnet.utils.analysis_utils import *
import pickle
import os
import networkx as nx
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm
from collections import defaultdict
from networkx.drawing.nx_pydot import graphviz_layout

def get_args():
    import argparse

    parser = argparse.ArgumentParser()
    # File I/O
    parser.add_argument(
        "--input-file",
        type=str,
        default="data/pre-process/syntrees/synthetic-trees.json.gz",
        help="Input file for the generated synthetic trees (*.json.gz)",
    )
    parser.add_argument(
        "--skeleton-file",
        type=str,
        default="results/viz/skeletons.pkl",
        help="Input file for the skeletons of syntree-file",
    )   
    parser.add_argument(
        "--skeleton-canonical-file",
        type=str,
        help="If given, use the keys as skeleton classes",
    )      
    parser.add_argument(
        "--rxns_collection_file",
        type=str,
        default="data/assets/reaction-templates/reactions_hb.json.gz",
    )    
    parser.add_argument(
        "--visualize-dir",
        type=str,
        default="",
        help="Input file for the skeletons of syntree-file",
    )
    # Visualization args
    parser.add_argument(
        "--min_count",
        type=int,
        default=10
    )
    parser.add_argument(
        "--num_to_vis",
        type=int,
        default=10
    )   

    # Processing
    parser.add_argument("--ncpu", type=int, help="Number of cpus")
    return parser.parse_args()   


if __name__ == "__main__":
    args = get_args()

    if os.path.exists(args.skeleton_file):
        skeletons = pickle.load(open(args.skeleton_file, 'rb'))
    else:
        syntree_collection = SyntheticTreeSet()
        syntrees = syntree_collection.load(args.input_file)        
        rxns = ReactionSet().load(args.rxns_collection_file).rxns        
        print("finished loading")
        syntrees = reorder_syntrees(syntrees, rxns) # make sure reactant order is correct        
        sts = []
        for st in tqdm(syntree_collection.sts):
            if st: 
                try:
                    st.build_tree()
                except:
                    breakpoint()
                sts.append(st)
            else:
                breakpoint()        
        # use the train set to define the skeleton classes
        if args.skeleton_canonical_file:
            skeletons = pickle.load(open(args.skeleton_canonical_file, 'rb'))
            class_nums = {k: len(skeletons[k]) for k in skeletons}
        else:
            skeletons = {}
        lookup = {}
        for i, st in tqdm(enumerate(sts), desc="serializing trees"):
            sk = Skeleton(st, -1)
            ans = []
            try:
                serialize(sk.tree, sk.tree_root, ans)
            except:
                breakpoint()
            if ','.join(ans) not in lookup:
                lookup[','.join(ans)] = st        
                skeletons[st] = [st]
            else:
                skeletons[lookup[','.join(ans)]].append(st)
        if args.skeleton_canonical_file:
            if list(class_nums.keys()) != list(skeletons.keys()):
                breakpoint()
            for k in class_nums:
                skeletons[k] = skeletons[k][class_nums[k]:]
        for k, v in skeletons.items():
            print(f"count: {len(v)}") 
        pickle.dump(skeletons, open(args.skeleton_file, 'wb+'))    
    breakpoint()
    if args.visualize_dir:
        os.makedirs(args.visualize_dir, exist_ok=True)
        # count_bbs(args, skeletons)
        # count_rxns(args, skeletons)
        vis_skeletons(args, skeletons)
        count_skeletons(args, skeletons)
        