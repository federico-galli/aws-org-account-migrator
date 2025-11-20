#!/bin/bash
./batch_migration.py \
    --csv-file accounts.csv \
    --source-profile <your-source-profile> \
    --target-profile <your-target-profile> \
    --target-ou-id <your-target-ou-id> \
    --max-failures 3 \
    --log-file migration_errors.log
