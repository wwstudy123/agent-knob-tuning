SELECT count(sbtest12.c) as count_value_c,count(sbtest14.pad) as count_value_pad,count(sbtest14.c) as count_value_c FROM sbtest14,sbtest12 WHERE sbtest14.id = sbtest12.id LIMIT 10;
