-- using 1779287821 as a seed to the RNG


select
	o_orderpriority,
	count(*) as order_count
from
	orders
where
<<<<<<< HEAD
	o_orderdate >= date '1997-08-01'
	and o_orderdate < date '1997-08-01' + interval '3' month
=======
	o_orderdate >= '1997-08-01'
	and o_orderdate < '1997-08-01' + interval 3 month
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
	and exists (
		select
			*
		from
			lineitem
		where
			l_orderkey = o_orderkey
			and l_commitdate < l_receiptdate
	)
group by
	o_orderpriority
order by
	o_orderpriority;
<<<<<<< HEAD
limit -1;
=======
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
