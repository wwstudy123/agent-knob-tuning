SELECT min(sbtest8.k) as minimum_value_k,avg(sbtest1.id) as average_value_id FROM sbtest1,sbtest8 WHERE sbtest1.id = sbtest8.id LIMIT 10;
