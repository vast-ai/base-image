#!/bin/bash

# Remove the blocker and leave supervisord to run
rm -f /.provisioning
wait $supervisord_pid