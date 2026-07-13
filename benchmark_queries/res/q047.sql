SELECT id,min(id) as minimum_value_id FROM sbtest10 WHERE sbtest10.id = 445149 and sbtest10.id = 779367 GROUP BY id;
