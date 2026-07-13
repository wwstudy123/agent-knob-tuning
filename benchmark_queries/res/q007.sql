SELECT max(sbtest10.k) as maximum_value_k,max(sbtest6.k) as maximum_value_k FROM sbtest6,sbtest10 WHERE sbtest6.id = sbtest10.id LIMIT 10;
