-- using 1779287821 as a seed to the RNG


select
	c_count,
	count(*) as custdist
from
	(
		select
			c_custkey,
<<<<<<< HEAD
			count(o_orderkey)
=======
			count(o_orderkey) as c_count
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
		from
			customer left outer join orders on
				c_custkey = o_custkey
				and o_comment not like '%special%packages%'
		group by
			c_custkey
<<<<<<< HEAD
	) as c_orders (c_custkey, c_count)
=======
	) as c_orders
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
group by
	c_count
order by
	custdist desc,
	c_count desc;
<<<<<<< HEAD
limit -1;
=======
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
