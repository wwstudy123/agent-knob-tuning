<<<<<<< HEAD
-- using 1779287821 as a seed to the RNG


select
	o_year,
	sum(case
		when nation = 'CHINA' then volume
		else 0
	end) / sum(volume) as mkt_share
from
	(
		select
			extract(year from o_orderdate) as o_year,
			l_extendedprice * (1 - l_discount) as volume,
			n2.n_name as nation
		from
			part,
			supplier,
			lineitem,
			orders,
			customer,
			nation n1,
			nation n2,
			region
		where
			p_partkey = l_partkey
			and s_suppkey = l_suppkey
			and l_orderkey = o_orderkey
			and o_custkey = c_custkey
			and c_nationkey = n1.n_nationkey
			and n1.n_regionkey = r_regionkey
			and r_name = 'ASIA'
			and s_nationkey = n2.n_nationkey
			and o_orderdate between date '1995-01-01' and date '1996-12-31'
			and p_type = 'STANDARD POLISHED STEEL'
	) as all_nations
group by
	o_year
order by
	o_year;
limit -1;
=======
SELECT
    o_year,
    SUM(CASE WHEN nation = 'CHINA' THEN volume ELSE 0 END) / SUM(volume) AS mkt_share
FROM
(
    SELECT
        YEAR(o_orderdate) AS o_year,
        l_extendedprice * (1 - l_discount) AS volume,
        n2.n_name AS nation
    FROM
        part,
        supplier,
        lineitem,
        orders,
        customer,
        nation n1,
        nation n2,
        region
    WHERE
        p_partkey = l_partkey
        AND s_suppkey = l_suppkey
        AND l_orderkey = o_orderkey
        AND o_custkey = c_custkey
        AND c_nationkey = n1.n_nationkey
        AND n1.n_regionkey = r_regionkey
        AND r_name = 'ASIA'
        AND s_nationkey = n2.n_nationkey
        AND o_orderdate BETWEEN '1995-01-01' AND '1996-12-31'
        AND p_type = 'STANDARD POLISHED STEEL'
) AS all_nations
GROUP BY o_year
ORDER BY o_year;
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
