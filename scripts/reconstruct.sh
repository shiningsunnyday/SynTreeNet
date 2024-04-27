if [[ $1 -eq 1 ]]; then
    ncpu=1;
    batch_size=50;
else
    ncpu=100;
    batch_size=100;
fi

python scripts/reconstruct-targets.py \
    --skeleton-set-file results/viz/top_1000/skeletons-top-1000-valid.pkl \
    --ckpt-rxn /home/msun415/SynTreeNet/results/logs/gnn//gnn/version_38/ \
    --ckpt-bb /home/msun415/SynTreeNet/results/logs/gnn//gnn/version_37/ \
    --ckpt-recognizer /home/msun415/SynTreeNet/results/logs/recognizer/1714155615.1292312/ckpts.epoch=3-val_loss=0.15.ckpt \
    --hash-dir results/hash_table-bb=1000-prods=2_new/ \
    --out-dir /home/msun415/SynTreeNet/results/viz/top_1000 \
    --top-k 3 \
    --test-correct-method reconstruct \
    --filter-only rxn bb \
    --top-bbs-file results/viz/programs/program_cache-bb=1000-prods=2/bblocks-top-1000.txt \
    --ncpu $ncpu \
    --batch-size $batch_size