-- using 1779287821 as a seed to the RNG


select
	s_acctbal,
	s_name,
	n_name,
	p_partkey,
	p_mfgr,
	s_address,
	s_phone,
	s_comment
from
	part,
	supplier,
	partsupp,
	nation,
	region
where
	p_partkey = ps_partkey
	and s_suppkey = ps_suppkey
	and p_size = 14
	and p_type like '%COPPER'
	and s_nationkey = n_nationkey
	and n_regionkey = r_regionkey
	and r_name = 'MIDDLE EAST'
	and ps_supplycost = (
		select
			min(ps_supplycost)
		from
			partsupp,
			supplier,
			nation,
			region
		where
			p_partkey = ps_partkey
			and s_suppkey = ps_suppkey
			and s_nationkey = n_nationkey
			and n_regionkey = r_regionkey
			and r_name = 'MIDDLE EAST'
	)
order by
	s_acctbal desc,
	n_name,
	s_name,
<<<<<<< HEAD
	p_partkey;
limit 100;
=======
	p_partkey limit 100;
>>>>>>> e25738c028a8ecabb540f79af544ad70c48ac9e8
