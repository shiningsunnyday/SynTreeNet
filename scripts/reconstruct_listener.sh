for ((i =1; i <= $1; i++));
do
python -u scripts/reconstruct_listener.py \
    --proc_id $i \
    --filename input_surrogate.txt \
    --output_filename output_surrogate.txt \
    --skeleton-set-file results/viz/top_1000/skeletons-top-1000.pkl \
    --ckpt-rxn /ssd/msun415/surrogate/version_38/ \
    --ckpt-bb /ssd/msun415/surrogate/version_37/ \
    --ckpt-recognizer /ssd/msun415/recognizer/ckpts.epoch=3-val_loss=0.15.ckpt \
    --hash-dir results/hash_table-bb=1000-prods=2_new/ \
    --out-dir /home/msun415/SynTreeNet/results/viz/top_1000 \
    --top-k 3 \
    --test-correct-method reconstruct \
    --strategy topological \
    --filter-only rxn bb \
    --top-bbs-file results/viz/programs/program_cache-bb=1000-prods=2/bblocks-top-1000.txt &
done
