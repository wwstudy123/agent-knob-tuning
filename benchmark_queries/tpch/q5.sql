-- using 1779287821 as a seed to the RNG


select
	n_name,
	sum(l_extendedprice * (1 - l_discount)) as revenue
from
	customer,
	orders,
	lineitem,
	supplier,
	nation,
	region
where
	c_custkey = o_custkey
	and l_orderkey = o_orderkey
	and l_suppkey = s_suppkey
	and c_nationkey = s_nationkey
	and s_nationkey = n_nationkey
	and n_regionkey = r_regionkey
	and r_name = 'ASIA'
<<<<<<< HEAD
	and o_orderdate >= date '1996-01-01'
	and o_orderdate < date '1996-01-01' + interval '1' year
=======
	and o_orderdate >= '1996-01-01'
	and o_orderdate < '1996-01-01' + interval 1 year
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
group by
	n_name
order by
	revenue desc;
<<<<<<< HEAD
limit -1;
=======
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
