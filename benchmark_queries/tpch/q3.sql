-- using 1779287821 as a seed to the RNG


select
	l_orderkey,
	sum(l_extendedprice * (1 - l_discount)) as revenue,
	o_orderdate,
	o_shippriority
from
	customer,
	orders,
	lineitem
where
	c_mktsegment = 'MACHINERY'
	and c_custkey = o_custkey
	and l_orderkey = o_orderkey
<<<<<<< HEAD
	and o_orderdate < date '1995-03-30'
	and l_shipdate > date '1995-03-30'
=======
	and o_orderdate < '1995-03-30'
	and l_shipdate > '1995-03-30'
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
group by
	l_orderkey,
	o_orderdate,
	o_shippriority
order by
	revenue desc,
<<<<<<< HEAD
	o_orderdate;
limit 10;
=======
	o_orderdate limit 10;
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
