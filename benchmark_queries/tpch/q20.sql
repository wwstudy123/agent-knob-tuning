-- using 1779287821 as a seed to the RNG


select
	s_name,
	s_address
from
	supplier,
	nation
where
	s_suppkey in (
		select
			ps_suppkey
		from
			partsupp
		where
			ps_partkey in (
				select
					p_partkey
				from
					part
				where
					p_name like 'royal%'
			)
			and ps_availqty > (
				select
					0.5 * sum(l_quantity)
				from
					lineitem
				where
					l_partkey = ps_partkey
					and l_suppkey = ps_suppkey
<<<<<<< HEAD
					and l_shipdate >= date '1997-01-01'
					and l_shipdate < date '1997-01-01' + interval '1' year
=======
					and l_shipdate >= '1997-01-01'
					and l_shipdate < '1997-01-01' + interval 1 year
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
			)
	)
	and s_nationkey = n_nationkey
	and n_name = 'JAPAN'
order by
	s_name;
<<<<<<< HEAD
limit -1;
=======
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
