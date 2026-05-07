#!/bin/bash
CI_NODE_TOTAL=${CI_NODE_TOTAL:-1}
CI_NODE_INDEX=${CI_NODE_INDEX:-1}

# Read stdin into an array, one entry per line
input=()
while IFS='' read -r value; do
    input+=("$value")
done

# Determine the hashes that belong to this node
hashes=()
node_index=1
for input in "${input[@]}"; do
    if [ $node_index -eq $CI_NODE_INDEX ]; then
        hashes+=("${input}")
    fi
    node_index=$((node_index + 1))
    if [ $node_index -gt $CI_NODE_TOTAL ]; then
        node_index=1
    fi
done

# Output the hashes that belong to this node
for hash in "${hashes[@]}"; do
    echo "$hash"
done
