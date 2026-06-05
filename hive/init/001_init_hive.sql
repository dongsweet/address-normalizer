CREATE DATABASE IF NOT EXISTS default;

CREATE TABLE IF NOT EXISTS default.ysk_datahub_address_standed (
  jxkid STRING,
  cjd STRING,
  rjxksj STRING,
  xxdz STRING,
  row_num_id STRING,
  src_address STRING,
  stand_address STRING,
  city STRING,
  county STRING,
  develop_area STRING,
  town STRING,
  community STRING,
  village_group STRING,
  bus_area STRING,
  road STRING,
  sub_road STRING,
  road_no STRING,
  subroad_no STRING,
  poi STRING,
  building STRING,
  `unit` STRING,
  floor STRING,
  room STRING,
  part_path STRING
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY '\t'
STORED AS TEXTFILE;

TRUNCATE TABLE default.ysk_datahub_address_standed;

LOAD DATA LOCAL INPATH '/opt/hive/data/ysk_datahub_address_standed.tsv'
OVERWRITE INTO TABLE default.ysk_datahub_address_standed;
