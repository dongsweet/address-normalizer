CREATE DATABASE IF NOT EXISTS address_normalizer;

USE address_normalizer;

DROP TABLE IF EXISTS ysk_datahub_address_standed;

CREATE TABLE ysk_datahub_address_standed (
    jxkid VARCHAR(128),
    cjd VARCHAR(255),
    rjxksj VARCHAR(64),
    xxdz STRING,
    row_num_id VARCHAR(64),
    src_address STRING,
    stand_address STRING,
    city VARCHAR(64),
    county VARCHAR(64),
    develop_area VARCHAR(128),
    town VARCHAR(128),
    community VARCHAR(255),
    village_group VARCHAR(255),
    bus_area VARCHAR(255),
    road VARCHAR(255),
    sub_road VARCHAR(255),
    road_no VARCHAR(64),
    subroad_no VARCHAR(64),
    poi VARCHAR(255),
    building VARCHAR(64),
    unit VARCHAR(64),
    floor VARCHAR(64),
    room VARCHAR(64),
    part_path STRING
)
DUPLICATE KEY(jxkid)
DISTRIBUTED BY HASH(jxkid) BUCKETS 1
PROPERTIES (
    "replication_num" = "1"
);
