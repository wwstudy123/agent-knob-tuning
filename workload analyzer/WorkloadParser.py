from Parserbase import *
import configparser
import os
import sys
import warnings
import re
import psqlparse


def _strip_sql_file_header(content: str) -> str:
    lines = content.splitlines()
    while lines and lines[0].startswith("--"):
        lines.pop(0)
    return "\n".join(lines).lstrip("\n")


def _load_workload_sql_list(workload_path: str) -> list[str]:
    with open(workload_path, "r", encoding="utf-8", errors="ignore") as f:
        content = _strip_sql_file_header(f.read())
    parts = re.split(r";\s*\n", content)
    return [p.strip() for p in parts if p.strip()]


def _extract_tables_regex(sql: str) -> list[str]:
    tables = []
    seen = set()
    for match in re.finditer(
        r"(?is)\b(?:from|join)\s+(?:only\s+)?([`\"]?)([a-zA-Z_][\w]*)\1",
        sql,
    ):
        name = match.group(2).lower()
        if name not in seen:
            seen.add(name)
            tables.append(name)
    return tables


class WP2(WP):
    def __init__(self) -> None:
        self.dbs=None
        pass

    def _schema_table_names(self) -> set[str]:
        return {table.name for table in self.dbs.tables}

    def _extract_schema_tables(self, sql: str) -> list[str]:
        schema_tables = self._schema_table_names()
        tables = set()
        try:
            parsed = psqlparse.parse(sql + ";")
            if parsed:
                tables.update(parsed[0].tables() or [])
        except Exception:
            pass
        tables.update(_extract_tables_regex(sql))

        normalized = []
        seen = set()
        for table_name in tables:
            name = table_name.lower()
            if name in schema_tables and name not in seen:
                seen.add(name)
                normalized.append(name)
        return normalized
    
    # workload analysis function
    def parse_workload(self,workload_path):
        if self.dbs==None:
            print("fatal error: dbs not initialization correctly.")
            return
        else:
            read_cnt=0
            write_cnt=0
            predicate_num=0
            group_by_num=0
            order_by_num=0
            aggr_num=0
            desc_num=0
            non_agg_count=0
            
            tbl_dict={}
            tbl_col_dict={}
            predicate_dict={}
            predicate_type=["=",">","<",">=","<="]
            for i in predicate_type:
                predicate_dict[i]=0

            sql_list = _load_workload_sql_list(workload_path)
            if not sql_list:
                print(f"fatal error: no SQL statements found in {workload_path}")
                return

            for sql in sql_list:
                real_tb_used = self._extract_schema_tables(sql)

                for table_name in real_tb_used:
                    if table_name not in tbl_dict.keys():
                        tbl_dict[table_name]=1
                        tbl_col_dict[table_name]={}
                        tb_tmp=self.dbs.getTableByName(table_name)
                        if tb_tmp is None:
                            continue
                        for it in tb_tmp.col:
                            tbl_col_dict[table_name][it.name]=0
                    else:
                        tbl_dict[table_name]+=1
                
                match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
                
                if match:
                    columns_part = match.group(1).strip()
                    agg_pattern = re.compile(
                        r'\b(COUNT|SUM|AVG|MAX|MIN|STDDEV|VARIANCE|GROUP_CONCAT)\s*\(',
                        re.IGNORECASE,
                    )
                    if columns_part == '*':
                        non_agg_count += 1
                        warnings.warn(
                            "Detected SELECT * usage, which may affect performance and result in unnecessary column returns",
                            category=RuntimeWarning,
                        )
                    else:
                        columns = [col.strip() for col in columns_part.split(',')]
                        for col in columns:
                            if not agg_pattern.search(col):
                                non_agg_count += 1
    
                simple_sql_token_list=re.split(r'[\(,;\s\)\n\t]+',sql)
                if simple_sql_token_list.__contains__("")==True:
                    simple_sql_token_list.remove("")
                # print(simple_sql_token_list)
                cnt_bool=False
                #  Query Semantic Features
                for id,j in enumerate(simple_sql_token_list):
                    if cnt_bool==False:
                        if j.upper()=='SELECT':
                            read_cnt+=1
                            cnt_bool=True
                        if j.upper()=='UPDATE' or j.upper()=='INSERT':
                            write_cnt+=1
                            cnt_bool=True
                    
                    if j.upper()=='AND' or j.upper()=='OR' or j.upper()=="WHERE":
                        predicate_num+=1
                    elif j.upper()=='GROUP' and id + 1 < len(simple_sql_token_list) and simple_sql_token_list[id+1].upper()=="BY":
                        group_by_num+=1
                    elif j.upper()=='ORDER' and id + 1 < len(simple_sql_token_list) and simple_sql_token_list[id+1].upper()=="BY":
                        order_by_num+=1
                    elif j.upper()=="SUM" or j.upper()=="MIN" or j.upper()=="MAX" or j.upper()=="AVG":
                        aggr_num+=1
                    elif j.upper()=="DESC":
                        desc_num+=1
                    elif j in predicate_type:
                        predicate_dict[j]+=1
                    else:
                        pass
                        
                # Data Access Features
                for token in simple_sql_token_list:
                    for tb_tmp in real_tb_used:
                        if tb_tmp not in tbl_col_dict:
                            continue
                        for col_tmp in tbl_col_dict[tb_tmp].keys():
                            if token==col_tmp:
                                tbl_col_dict[tb_tmp][col_tmp]+=1
                    tmp_res=re.match(".+\..+",token)
                    if tmp_res!=None:
                        table_ref, col_ref = tmp_res.group().split(".", 1)
                        if table_ref in tbl_col_dict and col_ref in tbl_col_dict[table_ref]:
                            tbl_col_dict[table_ref][col_ref]+=1
        maxi=""
        maxv=0
        mini=""
        minv=100000000    
        sumv=0            

        
        for table in self.dbs.tables:
            if table.name not in tbl_dict.keys():
                tbl_dict[table.name]=0
                tbl_col_dict[table.name]={}
                for it in table.col:
                    tbl_col_dict[table.name][it.name]=0
            
        for i in list(tbl_dict.keys()):
            sumv+=tbl_dict[i]
            if tbl_dict[i]>maxv:
                maxv=tbl_dict[i]
                maxi=i
            if tbl_dict[i]<minv:
                minv=tbl_dict[i]
                mini=i
                
        workload_size = len(sql_list)
        print("type of workload :",workload_path)
        print("sample SQL1:",re.split(r'[,;\s\n\t\(\)]+',sql_list[0])[:20])
        if len(sql_list) > 1:
            print("sample SQL2:",re.split(r'[,;\s\n\t\(\)]+',sql_list[1])[:20])
        print("size of workload :",workload_size)
        print("read write ratio : "+str(read_cnt)+"|"+str(write_cnt)+"  "+str(read_cnt/(write_cnt+read_cnt)))
        print("group by ratio : "+str(group_by_num/(write_cnt+read_cnt)))
        print("order by ratio : "+str(order_by_num/(write_cnt+read_cnt)))
        print("aggregation ratio : "+str(aggr_num/(write_cnt+read_cnt)))
        print("average predicate num per SQL :",str(predicate_num/(read_cnt+write_cnt)))
        print("max visited table :",maxi,str(maxv/sumv))
        print("min visited table :",mini,str(minv/sumv))
        
        print("average table access count :",sumv/workload_size)
        print("average item returned count per query :",non_agg_count/workload_size)
        if order_by_num:
            print("order by logic ratio :",(order_by_num-desc_num)/order_by_num,"(asc):",desc_num/order_by_num,"(desc)")
        else:
            print("order by logic ratio : N/A")
        
        print("where clause comparison condition ratio :")
        predicate_total = sum(predicate_dict.values())
        if predicate_total:
            for i in predicate_type:
                print("\t",i,predicate_dict[i]/predicate_total)
        else:
            print("\t(no comparison predicates detected)")
        
        print("table access pattern :")
        # tbl_dict record the access patterns of each table and column
        for i in tbl_dict:
            print("\t",i,str(tbl_dict[i])+"|"+str(sumv),"\t",tbl_dict[i]/sumv)
            tmp_sum=0
            for j in tbl_col_dict[i]:
                tmp_sum+=tbl_col_dict[i][j]
            if tmp_sum==0:
                continue
            for j in tbl_col_dict[i]:
                # print('\t',j)
                print("\t\t",j,str(tbl_col_dict[i][j])+"|"+str(tmp_sum),"\t",tbl_col_dict[i][j]/tmp_sum)
        print()
        
import argparse

if __name__=='__main__':

    config = configparser.ConfigParser()
    config.read('./config.ini')
    defaults = {
        "workload_file": "./input.json",
        "config_file": "./input.json",
        "output_file": "./workload_features"
    }
    if config.has_section('workload analyzer'):
        defaults.update(config['workload analyzer'])

    parser = argparse.ArgumentParser()
    parser.add_argument('--workload_file', type=str, default=defaults['workload_file'])
    parser.add_argument('--config_file', type=str, default=defaults['config_file'])
    parser.add_argument('--output', type=str, default=defaults['output_file'])
    args = parser.parse_args()
    print(args)

    if args.output:
        sys.stdout = open(args.output, 'w')
    
    files=[args.workload_file]
    
    wp=WP2()
    wp.parse_schema(args.config_file)
    # print(wp.dbs.toStr())
    # print(wp.dbs.getTableByName('lineitem'))
    # print(type(wp.dbs.getTableByName('lineitem').col))
    for i in files:
        print(i)
        wp.parse_workload(i)