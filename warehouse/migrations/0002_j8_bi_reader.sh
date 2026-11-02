# shellcheck shell=bash
# Creates Power BI's account on a warehouse created before J8, with the very
# script that new warehouses run at first start: it is written to be run
# again safely. Runs inside the warehouse container (make migrate-warehouse),
# where init/ is mounted and BI_READER_PASSWORD is set.
# shellcheck source=/dev/null
. /docker-entrypoint-initdb.d/04_bi_reader.sh
