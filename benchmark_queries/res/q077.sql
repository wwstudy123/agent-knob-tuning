SELECT count(sbtest47.pad) as count_value_pad,min(sbtest47.id) as minimum_value_id,min(sbtest47.k) as minimum_value_k FROM sbtest21,sbtest47 WHERE sbtest21.id = sbtest47.id LIMIT 10;
