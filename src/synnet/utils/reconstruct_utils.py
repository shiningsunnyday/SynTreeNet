from synnet.data_generation.preprocessing import BuildingBlockFileHandler, ReactionTemplateFileHandler
from synnet.visualize.drawers import MolDrawer, RxnDrawer
from synnet.visualize.writers import SynTreeWriter, SkeletonPrefixWriter
from synnet.visualize.visualizer import SkeletonVisualizer
from synnet.encoding.distances import cosine_distance
from synnet.models.common import load_gnn_from_ckpt, find_best_model_ckpt
from synnet.models.gnn import PtrDataset
from synnet.models.mlp import nn_search_list
from synnet.MolEmbedder import MolEmbedder
from synnet.utils.predict_utils import mol_fp, tanimoto_similarity
from synnet.utils.analysis_utils import serialize_string
from synnet.policy import RxnPolicy
import rdkit.Chem as Chem
from synnet.config import DATA_PREPROCESS_DIR, DATA_RESULT_DIR, MAX_PROCESSES, MAX_DEPTH, NUM_POSS, DELIM
from synnet.utils.data_utils import ReactionSet, SyntheticTreeSet, Skeleton, SkeletonSet, Program
from pathlib import Path
import numpy as np
import networkx as nx
from typing import Tuple
from torch_geometric.data import Data
import torch
import os
import yaml
import json
import gzip
from copy import deepcopy


def get_metrics(targets, all_sks):
    assert len(targets) == len(all_sks)
    total_incorrect = {} 
    if args.forcing_eval:
        correct_summary = {}
    else:
        total_correct = {}                     
        
    for (sk_true, smi), sks in zip(targets, all_sks):
        tree_id = str(np.array(sks[0].tree.edges))
        if tree_id not in total_incorrect: 
            total_incorrect[tree_id] = {}
        if not args.forcing_eval and tree_id not in total_correct:
            total_correct[tree_id] = {'correct': 0, 'total': 0, 'all': []}
        if args.forcing_eval and tree_id not in correct_summary:
            correct_summary[tree_id] = {'sum_pool_correct': 0, 'total_pool': 0}            
        if args.forcing_eval:            
            best_correct_steps = []            
            for sk in sks:
                correct_steps = test_correct(sk, sk_true, rxns, method='preorder', forcing=True)
                if sum(correct_steps) >= sum(best_correct_steps):
                    best_correct_steps = correct_steps                    
            correct_summary[tree_id]['sum_pool_correct'] += sum(best_correct_steps)
            correct_summary[tree_id]['total_pool'] += len(best_correct_steps)                
            correct_summary[tree_id]['step_by_step'] = correct_summary[tree_id]['sum_pool_correct']/correct_summary[tree_id]['total_pool']                              
            summary = {k: v['step_by_step'] for (k, v) in correct_summary.items()}
            print(f"step-by-step: {summary}")                
        else:
            correct = False
            for sk in sks:
                match = test_correct(sk, sk_true, rxns, method=args.test_correct_method)
                if args.test_correct_method == 'postorder':
                    correct = match
                elif args.test_correct_method == 'preorder':
                    correct, incorrect = match                        
                    if not correct:
                        update(total_incorrect[tree_id], incorrect)                    
                else:
                    assert args.test_correct_method == 'reconstruct'
                    correct = max(correct, match)
                if correct == 1:
                    break
            total_correct[tree_id]['correct'] += correct
            total_correct[tree_id]['total'] += 1
            total_correct[tree_id]['all'] += [correct]
            # print(f"tree: {sk.tree.edges} total_incorrect: {total_incorrect}")
            summary = {k: v['correct']/v['total'] for (k, v) in total_correct.items()}
            if args.test_correct_method == 'preorder':
                print(f"total summary: {summary}, total incorrect: {total_incorrect}")     
            else:
                print(f"total summary: {summary}")
    if args.forcing_eval:
        return correct_summary
    else:
        return total_correct, total_incorrect



def decode(sk, smi): 
    sk.clear_tree(forcing=args.forcing_eval)
    sk.modify_tree(sk.tree_root, smiles=smi)              
    if args.mermaid:
        skviz = lambda sk: SkeletonVisualizer(skeleton=sk, outfolder=args.out_dir).with_drawings(mol_drawer=MolDrawer, rxn_drawer=RxnDrawer)                       
    else:
        skviz = None
    # print(f"begin decoding {smi}")
    if 'rxn_models' in globals():
        rxn_gnn = rxn_models[sk.index]
    else:
        rxn_gnn = globals()['rxn_gnn']

    if 'bb_models' in globals():
        bb_gnn = bb_models[sk.index]
    else:
        bb_gnn = globals()['bb_gnn']
    try:
        sks = wrapper_decoder(args, sk, rxn_gnn, bb_gnn, bb_emb, rxn_templates, bblocks, skviz)    
        ans = serialize_string(sk.tree, sk.tree_root)        
        print(f"done decoding {smi} {ans}")
    except:
        sks = None
    return sks


def format_metrics(metrics, cum=False):
    res = ""
    for k, v in metrics.items():
        res += k + '\n'
        res += json.dumps(v) + '\n'
        res += '\n'

    if cum:
        cum = []
        for k in metrics:
            cum += metrics[k]['all']
        score = np.mean(cum)
        num = len(cum)
        res += f"Total: {score}/{num}\n"
    return res


def load_from_dir(dir, constraint):
    models = {}
    for version in os.listdir(dir):        
        hparams_filepath = os.path.join(dir, version, 'hparams.yaml')        
        hparams_file = yaml.safe_load(open(hparams_filepath))      
        match = True
        for k in constraint:
            if str(constraint[k]) != str(hparams_file[k]):
                match = False
        if match:       
            fpaths = list(Path(os.path.join(dir, version)).glob("*.ckpt"))
            if len(fpaths) != 1:
                print(f"{version} has {len(fpaths)} ckpts")
                continue
            models[int(hparams_file['datasets'])] = load_gnn_from_ckpt(fpaths[0])
    return models



def test_skeletons(args, skeleton_set):
    if args.ckpt_dir:
        SKELETON_INDEX = []
        for ind in range(len(skeleton_set.sks)):
            sk = skeleton_set.sks[ind]      
            if 'rxn_models' in globals() and 'bb_models' in globals():
                if ind in globals()['rxn_models'] and ind in globals()['bb_models']:
                    SKELETON_INDEX.append(ind)
            else:
                SKELETON_INDEX.append(ind)
    else:        
        dirname = os.path.dirname(args.ckpt_rxn)
        config_file = os.path.join(dirname, 'hparams.yaml')
        config = yaml.safe_load(open(config_file))
        SKELETON_INDEX = list(map(int, config['datasets'].split(',')))
    return SKELETON_INDEX


def set_models(args, logger):
    logger.info("Start loading models from checkpoints...")  
    if args.ckpt_dir and os.path.isdir(args.ckpt_dir):
        constraint = {'valid_loss': 'accuracy_loss'}
        rxn_models = load_from_dir(args.ckpt_dir, constraint)
        globals()['rxn_models'] = rxn_models
        constraint = {'valid_loss': 'nn_accuracy_loss'}
        bb_models = load_from_dir(args.ckpt_dir, constraint)
        globals()['bb_models'] = bb_models    
    else:
        if not os.path.isfile(args.ckpt_rxn):
            best_ckpt = find_best_model_ckpt(args.ckpt_rxn, key="val_accuracy_loss")
            setattr(args, "ckpt_rxn", best_ckpt)
        rxn_gnn = load_gnn_from_ckpt(Path(args.ckpt_rxn))
        globals()['rxn_gnn'] = rxn_gnn
        if not os.path.isfile(args.ckpt_bb):
            best_ckpt = find_best_model_ckpt(args.ckpt_bb, key="val_nn_accuracy_loss")
            setattr(args, "ckpt_bb", best_ckpt)        
        bb_gnn = load_gnn_from_ckpt(Path(args.ckpt_bb))
        globals()['bb_gnn'] = bb_gnn    
    logger.info("...loading models completed.")    




def get_anc(cur, rxn_graph):
    lca = cur
    while rxn_graph.nodes[lca]['depth'] != MAX_DEPTH:
        ancs = list(rxn_graph.predecessors(lca))
        if ancs: 
            lca = ancs[0]
        else: 
            break         
    return lca     


def filter_imposs(args, rxn_graph, sk, cur, n):
    max_depth = max([rxn_graph.nodes[n]['depth'] for n in rxn_graph])
    paths = []
    mask_imposs = [False for _ in range(NUM_POSS)]            
    # use sk to filter out reaction type
    bi_mol = len(list(sk.tree.successors(n))) == 2
    for i in range(NUM_POSS):
        if bi_mol != (rxns[i].num_reactant == 2):
            mask_imposs[i] = True         
    if 'rxn' not in args.filter_only:
        return mask_imposs, None
    rxn_imposs = deepcopy(mask_imposs)    
    attr_name = 'rxn_id_forcing' if args.forcing_eval else 'rxn_id'
    # if max_depth < MAX_DEPTH or rxn_graph.nodes[cur]['depth'] == MAX_DEPTH:
    #     # try every reaction, and use existence of hash to filter possibilites            
    #     term = rxn_graph.nodes[cur][attr_name]
    #     for rxn_id in range(NUM_POSS):
    #         rxn_graph.nodes[cur][attr_name] = rxn_id
    #         p = Program(rxn_graph)
    #         path = os.path.join(args.hash_dir, p.get_path())
    #         mask_imposs[rxn_id] = mask_imposs[rxn_id] or not os.path.exists(path)
    #         if os.path.exists(path):
    #             paths.append(path)
    #         else:
    #             paths.append('')
    #     rxn_graph.nodes[cur][attr_name] = term
    # elif rxn_graph.nodes[cur]['depth'] < MAX_DEPTH:
    #     lca = get_anc(cur, rxn_graph)
    #     # use prog_graph to hash, navigate the file system
    #     term = rxn_graph.nodes[cur][attr_name]
    #     for rxn_id in range(NUM_POSS):
    #         rxn_graph.nodes[cur][attr_name] = rxn_id
    #         p = Program(rxn_graph)
    #         if 'path' not in rxn_graph.nodes[lca]:
    #             breakpoint()
    #         if rxn_graph.nodes[lca]['path'][-5:] == '.json': # prev lca exist              
    #             path_stem = rxn_graph.nodes[lca]['path'][:-5]                
    #             path_stem = Path(path_stem).stem
    #             path = os.path.join(args.hash_dir, path_stem, p.get_path())
    #             mask_imposs[rxn_id] = mask_imposs[rxn_id] or not os.path.exists(path)
    #             if os.path.exists(path):
    #                 paths.append(path)
    #             else:
    #                 paths.append('')                        
    #         else:
    #             mask_imposs[rxn_id] = True
    #             paths.append('')

    #     rxn_graph.nodes[cur][attr_name] = term     
    # if sum(mask_imposs) == NUM_POSS:
    #     mask_imposs = rxn_imposs    
    if rxn_graph.nodes[cur]['depth'] == 1:    
        base_case = False                    
        r_preds = list(rxn_graph.predecessors(cur))
        if len(r_preds) == 0:
            base_case = True
        else:
            r_pred = r_preds[0]
            pred = sk.pred(sk.pred(sk.pred(n)))
            depth = rxn_graph.nodes[r_pred]['depth']
            if depth > 2:
                base_case = True
        if base_case:
            paths = []
            for i in range(91):
                g = nx.DiGraph()
                g.add_node(0, rxn_id=i, depth=1)   
                path = Program(g).get_path()
                path = os.path.join(args.hash_dir, path)
                if os.path.exists(path):
                    paths.append(path)
                    rxn_imposs[i] = False
                else:
                    paths.append('')
                    rxn_imposs[i] = True
            return rxn_imposs, paths
        policy = RxnPolicy(4096+2*91, 2, 2*91, sk.subtree(pred), args.hash_dir, rxns)
        obs = np.zeros((4096+2*91,))        
        rxn_id = rxn_graph.nodes[r_pred]['rxn_id']
        obs[-91+rxn_id] = 1        
        mask, paths = policy.action_mask(obs, return_paths=True)
        mask_imposs = ~mask[:91]
    elif rxn_graph.nodes[cur]['depth'] == 2:
        pred = sk.pred(n)
        policy = RxnPolicy(4096+2*91, 2, 2*91, sk.subtree(pred), args.hash_dir, rxns)
        obs = np.zeros((4096+2*91,))        
        mask, paths = policy.action_mask(obs, return_paths=True)
        mask_imposs = ~mask[91:]
    else:
        mask_imposs = rxn_imposs
        paths = []
    return mask_imposs, paths


def fill_in(args, sk, n, logits_n, bb_emb, rxn_templates, bbs, top_bb=1):
    """
    if rxn
        detect if n is within MAX_DEPTH of root
        if not fill in as usual
        if yes, find LCA (MAX_DEPTH from root), then use the subtree to constrain possibilities
    else
        find LCA (MAX_DEPTH from n), then use it to constrain possibilities
    """            
    rxn_graph, node_map, _ = sk.rxn_graph()    
    if sk.rxns[n]:  
        cur = node_map[n]
        if rxn_graph.nodes[cur]['depth'] <= 2:
            mask_imposs, paths = filter_imposs(args, rxn_graph, sk, cur, n)
            assert sum(mask_imposs) < NUM_POSS # TODO: handle failure
            logits_n[-NUM_POSS:][mask_imposs] = float("-inf")                  
        else:
            paths = []
        rxn_id = logits_n[-NUM_POSS:].argmax(axis=-1).item()     
        # Sanity check for forcing eval
        sk.modify_tree(n, rxn_id=rxn_id, suffix='_forcing' if args.forcing_eval else '')
        sk.tree.nodes[n]['smirks'] = rxn_templates[rxn_id]
        rxn_graph.nodes[cur]['rxn_id'] = rxn_id                   
        # mask the intermediates so they're not considered on frontier
        for succ in sk.tree.successors(n):
            if not sk.leaves[succ]:
                sk.mask = [succ]
        if 'rxn' in args.filter_only:
            if len(paths):
                sk.tree.nodes[n]['path'] = paths[rxn_id]
        # print("path", os.path.join(args.hash_dir, p.get_path()))            
    else:           
        assert sk.leaves[n]
        emb_bb = logits_n[:-NUM_POSS]
        pred = list(sk.tree.predecessors(n))[0]           
        if 'bb' in args.filter_only:            
            if rxn_graph.nodes[node_map[pred]]['depth'] > MAX_DEPTH:
                exist = False
            else:
                path = sk.tree.nodes[pred]['path']     
                exist = os.path.exists(path)
        else:
            exist = False
        failed = False
        if exist:                
            e = str(node_map[pred])
            if rxn_graph.nodes[int(e)]['depth'] == 2:
                e = '1'
            else:
                assert rxn_graph.nodes[int(e)]['depth'] == 1
                e = '0'
            data = json.load(open(path))
            succs = list(sk.tree.successors(pred))
            second = sk.tree.nodes[n]['child'] == 'right'
            if e in data['bbs']:
                bbs_child = data['bbs'][e][int(second)]
            else:
                bbs_child = data['bbs'][f"{e}{DELIM}{int(second)}"]
                assert len(bbs_child) == 1
                bbs_child = bbs_child[0]
            if args.forcing_eval:
                if sk.tree.nodes[n]['smiles'] not in bbs_child:
                    bad = False
                    for m in sk.tree:
                        if 'rxn_id' in sk.tree.nodes[m]:
                            if sk.tree.nodes[m]['rxn_id_forcing'] != sk.tree.nodes[m]['rxn_id']:
                                bad = True
                    # if not bad:
                    #     breakpoint()
            indices = [bbs.index(smi) for smi in bbs_child]
            if len(indices) >= top_bb:
                bb_ind = nn_search_list(emb_bb, bb_emb[indices], top_k=top_bb).item()
                smiles = bbs[indices[bb_ind]]
            else:
                failed = True
        if not exist or failed:
            bb_ind = nn_search_list(emb_bb, bb_emb, top_k=top_bb).item()
            smiles = bbs[bb_ind]
        sk.modify_tree(n, smiles=smiles, suffix='_forcing' if args.forcing_eval else '')    



def dist(emb, bb_emb):
    dists = (emb-bb_emb).abs().sum(axis=-1)
    return dists.min()


def pick_node(sk, logits, bb_emb):
    """
    implement strategies here,
    each node returned will add a beam to decoding
    """
    # pick frontier-rxn with highest logit if there is frontier-rxn
    # else pick random bb
    best_conf = float("-inf")
    best_rxn_n = None
    for n in logits:
        if sk.rxns[n]:
            conf = logits[n][-NUM_POSS:].max()
            if conf > best_conf:
                best_conf = conf
                best_rxn_n = n
    if best_rxn_n is not None:
        return [best_rxn_n]
    else:
        dists = [dist(logits[n][:-NUM_POSS], bb_emb) for n in logits]
        n = list(logits)[np.argmin(dists)]
        return [n]
        # return [n for n in logits if not sk.rxns[n]]


@torch.no_grad()
def wrapper_decoder(args, sk, model_rxn, model_bb, bb_emb, rxn_templates, bblocks, skviz=None):
    top_k = args.top_k
    """Generate a filled-in skeleton given the input which is only filled with the target."""
    model_rxn.eval()
    model_bb.eval()
    # Following how SynNet reports reconstruction accuracy, we decode top-3 reactants, 
    # corresponding to the first bb chosen
    # To make the code more general, we implement this with a stack
    sks = [sk]    
    final_sks = []
    while len(sks):
        sk = sks.pop(-1)
        if ((~sk.mask) & (sk.rxns | sk.leaves)).any():
            """
            while there's reaction nodes or leaf building blocks to fill in
                compute, for each vacant reaction node or vacant building block, the possible
                predict on all of these
                pick the highest confidence one
                fill it in
            """        
            # print(f"decode step {sk.mask}")
            # prediction problem        
            _, X, _ = sk.get_state(rxn_target_down_bb=True, rxn_target_down=True)
            for i in range(len(X)):
                if i != sk.tree_root and not sk.rxns[i] and not sk.leaves[i]:
                    X[i] = 0
            
            edges = sk.tree_edges
            tree_edges = np.concatenate((edges, edges[::-1]), axis=-1)
            edge_input = torch.tensor(tree_edges, dtype=torch.int64)        
            if model_rxn.layers[0].in_channels != X.shape[1]:
                pe = PtrDataset.positionalencoding1d(32, len(X))
                x_input_rxn = np.concatenate((X, pe), axis=-1)
            else:
                x_input_rxn = X
            if model_bb.layers[0].in_channels != X.shape[1]:
                pe = PtrDataset.positionalencoding1d(32, len(X))
                x_input_bb = np.concatenate((X, pe), axis=-1)            
            else:
                x_input_bb = X
            data_rxn = Data(edge_index=edge_input, x=torch.Tensor(x_input_rxn))
            data_bb = Data(edge_index=edge_input, x=torch.Tensor(x_input_bb))
            logits_rxn = model_rxn(data_rxn)
            logits_bb = model_bb(data_bb)
            logits = {}
            frontier_nodes = [n for n in set(sk.frontier_nodes) if not sk.mask[n]]
            for n in frontier_nodes:
                if sk.rxns[n]:
                    logits[n] = logits_rxn[n]
                else:                
                    assert sk.leaves[n]               
                    logits[n] = logits_bb[n]        
            poss_n = pick_node(sk, logits, bb_emb)
            for n in poss_n:
                logits_n = logits[n].clone()
                sk_n = deepcopy(sk)
                first_bb = sk_n.leaves[n] and (sk_n.leaves)[sk_n.mask == 1].sum() == 0
                if top_k > 1 and first_bb: # first bb                
                    for k in range(1, 1+top_k):
                        sk_copy = deepcopy(sk_n)
                        fill_in(args, sk_copy, n, logits_n, bb_emb, rxn_templates, bblocks, top_bb=k)
                        sks.append(sk_copy)
                else:
                    fill_in(args, sk_n, n, logits_n, bb_emb, rxn_templates, bblocks, top_bb=1)
                    sks.append(sk_n)
                    if skviz is not None:
                        sk_viz_n = skviz(sk_n)
                        mermaid_txt = sk_viz_n.write(node_mask=sk_n.mask)             
                        mask_str = ''.join(map(str,sk_n.mask))
                        outfile = sk_viz_n.path / f"skeleton_{sk_n.index}_{mask_str}.md"  
                        SynTreeWriter(prefixer=SkeletonPrefixWriter()).write(mermaid_txt).to_file(outfile)      
                        print(f"Generated markdown file.", os.path.join(os.getcwd(), outfile))            
        else:
            final_sks.append(sk)
    # print(len(final_sks), "beams")
    return final_sks


def test_correct(sk, sk_true, rxns, method='preorder', forcing=False):
    if method == 'preorder':
        if forcing:
            for n in sk.tree:
                attrs = [attr for attr in list(sk.tree.nodes[n]) if '_forcing' in attr]
                for attr in attrs:
                    # we re-store the attributes containing predictions into 
                    # original attributes
                    sk.tree.nodes[n][attr[:-len('_forcing')]] = sk.tree.nodes[n][attr]
        total_incorrect = {}
        preorder = list(nx.dfs_preorder_nodes(sk.tree, source=sk.tree_root))
        correct = True
        seq_correct = []
        for i in preorder:                
            if sk.rxns[i]:
                if sk.tree.nodes[i]['rxn_id'] != sk_true.tree.nodes[i]['rxn_id']:
                    correct = False
                    total_incorrect[i] = 1                    
                seq_correct.append(i not in total_incorrect)
            elif sk.leaves[i]:
                if sk.tree.nodes[i]['smiles'] != sk_true.tree.nodes[i]['smiles']:
                    correct = False
                    total_incorrect[i] = 1   
                seq_correct.append(i not in total_incorrect)
        if forcing:                     
            return seq_correct
        else:
            return correct, total_incorrect
    elif method == 'postorder':
        # compute intermediates and target

        sk.reconstruct(rxns)
        smis = sk_true.tree.nodes[sk_true.tree_root]['smiles'].split(DELIM)
        smis = [Chem.CanonSmiles(smi) for smi in smis]
        correct = smi2 in smis
    else:
        assert method == 'reconstruct'
        sk.reconstruct(rxns)
        smiles = []
        for n in sk.tree:
            if 'smiles' in sk.tree.nodes[n]:
                if sk.tree.nodes[n]['smiles']:
                    smiles += sk.tree.nodes[n]['smiles'].split(DELIM)
        smi2 = Chem.CanonSmiles(sk_true.tree.nodes[sk_true.tree_root]['smiles'])
        sims = tanimoto_similarity(mol_fp(smi2), smiles)
        correct = int(max(sims) == 1)
    return correct


def update(dic_total, dic):
    for k in dic:
        if k not in dic_total:
            dic_total[k] = 0
        dic_total[k] += dic[k]


def load_data(args, logger=None):
    # ... reaction templates
    rxns = ReactionSet().load(args.rxns_collection_file).rxns
    if logger is not None:
        logger.info(f"Successfully read {args.rxns_collection_file}.")
    rxn_templates = ReactionTemplateFileHandler().load(args.rxn_templates_file)    

    # # ... building blocks
    bblocks = BuildingBlockFileHandler().load(args.building_blocks_file)
    # # A dict is used as lookup table for 2nd reactant during inference:
    # bblocks_dict = {block: i for i, block in enumerate(bblocks)}
    # logger.info(f"Successfully read {args.building_blocks_file}.")

    # # ... building block embedding
    # bblocks_molembedder = (
    #     MolEmbedder().load_precomputed(args.embeddings_knn_file).init_balltree(cosine_distance)
    # )
    # bb_emb = bblocks_molembedder.get_embeddings()    
    # bb_emb = torch.as_tensor(bb_emb, dtype=torch.float32)
    bb_emb = torch.FloatTensor(np.load(args.embeddings_knn_file))
    if logger is not None:
        logger.info(f"Successfully read {args.embeddings_knn_file}.")    
        logger.info("...loading data completed.")        
    globals()['rxns'] = rxns
    globals()['rxn_templates'] = rxn_templates    
    globals()['bblocks'] = bblocks
    globals()['bb_emb'] = bb_emb
    globals()['args'] = args


# For surrogate within GA


def surrogate(sk, fp, oracle):
    sks = decode(sk, fp)
    ans = 0.
    if sks is None:
        return 0.
    for sk in sks:
        sk.reconstruct(rxns)
        sk.visualize('/home/msun415/test.png')
        smi = sk.tree.nodes[sk.tree_root]['smiles']
        score = oracle(smi)
        print(f"oracle {smi} score {score}")
        ans = max(ans, score)    
    return ans