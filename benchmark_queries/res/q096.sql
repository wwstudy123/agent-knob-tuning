SELECT count(sbtest14.pad) as count_value_pad,count(sbtest19.pad) as count_value_pad,min(sbtest19.k) as minimum_value_k FROM sbtest14,sbtest19 WHERE sbtest14.id = sbtest19.id LIMIT 10;
