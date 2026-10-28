#!/bin/bash
if [[ -z $PORTAL_CONFIG ]]; then
  export PORTAL_CONFIG="localhost:1111:11111:/:Instance Portal|localhost:8080:18080:/:Jupyter|localhost:8080:8080:/terminals/1:Jupyter Terminal"
fi
