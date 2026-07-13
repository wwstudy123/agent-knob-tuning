SELECT pad,count(pad) as count_value_pad,max(k) as maximum_value_k FROM sbtest24 WHERE sbtest24.pad = 'xrLlvG80UyHR3tb1wXAtAwAU3ihwWAlgTxxIL4PtmKTMrI2KNGEZYgRqOix1' GROUP BY pad;
