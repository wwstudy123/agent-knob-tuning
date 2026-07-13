SELECT count(sbtest36.pad) as count_value_pad,count(sbtest21.pad) as count_value_pad FROM sbtest36,sbtest21 WHERE sbtest36.id = sbtest21.id LIMIT 10;
