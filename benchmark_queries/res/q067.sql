SELECT count(sbtest23.pad) as count_value_pad,count(sbtest42.pad) as count_value_pad FROM sbtest23,sbtest42 WHERE sbtest23.id = sbtest42.id and sbtest23.k = 817824 LIMIT 10;
