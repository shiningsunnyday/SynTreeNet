export OMP_NUM_THREADS=1
use_case='surrogate'
export PYTHONPATH="${HOME}/SynTreeNet/src"

for ((i =1; i <= $1; i++));
do
python -u scripts/reconstruct_listener.py \
    --proc_id $i \
    --filename input_${use_case}.txt \
    --output_filename output_${use_case}.txt \
    --skeleton-set-file /ssd/msun415/skeletons-top-1000-valid.pkl \
    --ckpt-rxn /ssd/msun415/surrogate/version_38/ \
    --ckpt-bb /ssd/msun415/surrogate/version_37/ \
    --ckpt-recognizer /ssd/msun415/recognizer/ckpts.epoch=3-val_loss=0.15.ckpt \
    --hash-dir /ssd/msun415/hash_table-bb=1000-prods=2_new/ \
    --out-dir $HOME/SynTreeNet/results/viz/top_1000 \
    --top-k 3 \
    --test-correct-method reconstruct \
    --strategy topological \
    --filter-only rxn bb \
    --top-bbs-file /ssd/msun415/bblocks-top-1000.txt &
done
