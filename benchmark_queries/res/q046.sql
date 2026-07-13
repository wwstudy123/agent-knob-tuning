SELECT k,id,max(k) as maximum_value_k FROM sbtest46 WHERE sbtest46.k = 256522 and sbtest46.pad = 'HTVzrdrcFCy3fXT9Vc4wHhvVHfFBzcodvmBc5Dk1SAGlkgsHRMWuosXVN0Gi' GROUP BY k,id ORDER BY id DESC;
