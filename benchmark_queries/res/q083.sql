SELECT count(sbtest5.c) as count_value_c,count(sbtest5.pad) as count_value_pad,max(sbtest40.id) as maximum_value_id FROM sbtest5,sbtest40 WHERE sbtest5.id = sbtest40.id LIMIT 10;
