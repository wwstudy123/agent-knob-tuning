SELECT max(sbtest29.id) as maximum_value_id,max(sbtest37.id) as maximum_value_id,count(sbtest37.c) as count_value_c FROM sbtest37,sbtest29 WHERE sbtest37.id = sbtest29.id LIMIT 10;
