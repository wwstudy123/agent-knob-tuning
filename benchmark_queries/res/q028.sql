SELECT min(sbtest32.id) as minimum_value_id,count(sbtest32.c) as count_value_c FROM sbtest26,sbtest32 WHERE sbtest26.id = sbtest32.id LIMIT 10;
