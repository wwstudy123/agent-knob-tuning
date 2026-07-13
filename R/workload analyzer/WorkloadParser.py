from Parserbase import *
import configparser
import os
import sys
import warnings
import psqlparse
import argparse

class WP2(WP):
    def __init__(self) -> None:
        self.dbs=None
        pass
    
    # 重写父类WP的方法：解析工作负载SQL
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
            
            # Set the output window environment to display all information
            pd.set_option('max_colwidth',None)
            df = pd.read_csv(workload_path, header=None,on_bad_lines='skip',sep = r'\s+\n',index_col=0,engine='python') 
            # df=pd.read_csv("seats_workload.txt",header=None)
            
            tokens=""
            for i in df.index.values:
                tokens+=i
                tokens+=" "
        
            # Using regular expressions to segment
            sql_list=re.split('[\s]*;[\n]*[\s]*',tokens)
            # token_list=re.split(r'[\(,;\s\)\n\t]+',tokens)
            # print(sql_list[-3:])
            for i in range(len(sql_list)):
                import psqlparse
                if sql_list[i]=="" or sql_list[i]==' ':
                    continue
                # print("i: ",sql_list[i])
                real_tb_used=psqlparse.parse(sql_list[i]+";")[0].tables()
                # print(real_tb_used)
                for table_name in real_tb_used:
                    if table_name not in tbl_dict.keys():
                        tbl_dict[table_name]=1
                        tbl_col_dict[table_name]={}
                        tb_tmp=self.dbs.getTableByName(table_name)
                        # print(table_name)
                        for it in tb_tmp.col:
                            tbl_col_dict[table_name][it.name]=0
                    else:
                        tbl_dict[table_name]+=1
                
                match = re.search(r'SELECT\s+(.*?)\s+FROM', sql_list[i], re.IGNORECASE)
                
                if match:
                    columns_part = match.group(1).strip()
                    if columns_part=='*':
                        non_agg_count+=1
                        warnings.warn(
                            "Detected SELECT * usage, which may affect performance and result in unnecessary column returns",
                            category=RuntimeWarning
                        )
                    else:
                        agg_pattern=re.compile(
                            r'\b(COUNT|SUM|AVG|MAX|MIN|STDDEV|VARIANCE|GROUP_CONCAT)\s*\(.*?\)',
                            re.IGNORECASE
                        )
                    columns = [col.strip() for col in columns_part.split(',')]
                    for col in columns:
                        if not agg_pattern.search(col):
                            non_agg_count+=1
                    # print(non_agg_count)
    
                simple_sql_token_list=re.split(r'[\(,;\s\)\n\t]+',sql_list[i])
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
                    elif j.upper()=='GROUP' and simple_sql_token_list[id+1].upper()=="BY":
                        group_by_num+=1
                    elif j.upper()=='ORDER' and simple_sql_token_list[id+1].upper()=="BY":
                        order_by_num+=1
                    elif j.upper()=="SUM" or j.upper()=="MIN" or j.upper()=="MAX" or j.upper()=="AVG":
                        aggr_num+=1
                    elif j.upper()=="DESC":
                        desc_num+=1
                    elif j in predicate_type:
                        predicate_dict[j]+=1
                    else:
                        # if j=='supplier':
                        #     print(simple_sql_token_list[id-1:id+5])
                        pass
                        
                # Data Access Features
                for token in simple_sql_token_list:
                    for tb_tmp in real_tb_used:
                        for col_tmp in tbl_col_dict[tb_tmp].keys():
                            if token==col_tmp:
                                # print("table_name : ",tb_tmp,"col_name : ",col_tmp)
                                tbl_col_dict[tb_tmp][col_tmp]+=1
                    tmp_res=re.match(".+\..+",token)
                    if tmp_res!=None:
                        # print(tmp_res.group().split("."))
                        if tmp_res.group().split(".")[0] in real_tb_used:
                            # print(tmp_res.group().split()[0],tmp_res.group().split()[1])
                            tbl_col_dict[tmp_res.group().split(".")[0]][tmp_res.group().split(".")[1]]+=1
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

        # 优化后的Prompt格式输出
        total_sql = tokens.count(";")
        total_rw = read_cnt + write_cnt
        
        # 工作负载基础概况
        print("#### 2.1 Basic Information")
        print(f"- **Workload Path**: `{workload_path}`")
        print(f"- **Total SQL Queries**: {total_sql}")
        # 将带有反斜杠的正则表达式运算提取到 f-string 外部，避免SyntaxError
        sample_sql_1 = re.split(r'[,;\s\n\t\(\)]+', str(df.iloc[0].name))
        sample_sql_2 = re.split(r'[,;\s\n\t\(\)]+', str(df.iloc[1].name))
        print(f"- **Sample SQL 1**: `{sample_sql_1}`")
        print(f"- **Sample SQL 2**: `{sample_sql_2}`")
        print()

        # 查询语义与聚合
        print("#### 2.2 Query Semantic & Aggregation")
        rw_ratio = read_cnt / total_rw if total_rw > 0 else 0
        print(f"- **Read/Write Ratio**: {rw_ratio:.3f} ({read_cnt} Reads / {write_cnt} Writes)")
        print(f"- **Group By Ratio**: {group_by_num / total_rw:.3f}" if total_rw > 0 else "- **Group By Ratio**: 0.000")
        print(f"- **Order By Ratio**: {order_by_num / total_rw:.3f}" if total_rw > 0 else "- **Order By Ratio**: 0.000")
        print(f"- **Aggregation Ratio**: {aggr_num / total_rw:.3f}" if total_rw > 0 else "- **Aggregation Ratio**: 0.000")
        print(f"- **Avg Items Returned Per Query**: {non_agg_count / total_sql:.3f}" if total_sql > 0 else "- **Avg Items Returned Per Query**: 0.000")
        print()

        # 谓词与过滤条件
        print("#### 2.3 Predicate & Condition")
        print(f"- **Avg Predicates Per SQL**: {predicate_num / total_rw:.3f}" if total_rw > 0 else "- **Avg Predicates Per SQL**: 0.000")
        
        asc_ratio = (order_by_num - desc_num) / order_by_num if order_by_num > 0 else 0
        desc_ratio = desc_num / order_by_num if order_by_num > 0 else 0
        print(f"- **Order By Logic**: ASC = {asc_ratio:.3f}, DESC = {desc_ratio:.3f}")
        
        print("- **Where Clause Comparison Condition Ratio**:")
        total_pred = sum(predicate_dict.values())
        for i in predicate_type:
            pred_ratio = predicate_dict[i] / total_pred if total_pred > 0 else 0
            print(f"  - `{i}` : {pred_ratio:.3f}")
        print()

        # 数据访问热点
        print("#### 2.4 Data Access Pattern")
        print(f"- **Avg Table Access Count Per Query**: {sumv / total_sql:.3f}" if total_sql > 0 else "- **Avg Table Access Count Per Query**: 0.000")
        print(f"- **Max Visited Table**: `{maxi}` (Ratio: {maxv / sumv:.3f})" if sumv > 0 else "- **Max Visited Table**: N/A")
        print(f"- **Min Visited Table**: `{mini}` (Ratio: {minv / sumv:.3f})" if sumv > 0 else "- **Min Visited Table**: N/A")
        print("- **Detailed Table & Column Access Distribution**:")
        print("  Format: `Table/Column Name: Hits/Total (Ratio)`") # 在列表前声明全局格式
        # 严格遵守 Markdown 列表缩进规范 (2个空格与4个空格)
        for i in tbl_dict:
            tb_ratio = tbl_dict[i] / sumv if sumv > 0 else 0
            # 表级输出格式精简
            print(f"  - `{i}` : {tbl_dict[i]}/{sumv} ({tb_ratio:.3f})")
            
            tmp_sum = sum(tbl_col_dict[i].values())
            if tmp_sum == 0:
                continue
                
            for j in tbl_col_dict[i]:
                col_ratio = tbl_col_dict[i][j] / tmp_sum
                # 列级输出格式精简：去除冗余文本，使用紧凑的括号表达比例
                print(f"    - {j}: {tbl_col_dict[i][j]}/{tmp_sum} ({col_ratio:.3f})")


if __name__=='__main__':

    # 解析配置文件
    # 优先级：默认值 < 配置文件 < 命令行参数
    config = configparser.ConfigParser()
    config.read('./config.ini')
    # 硬编码设置默认值
    defaults = {
        "workload_file": "./input.json",
        "config_file": "./input.json",
        "output_file": "./workload_features"
    }
    # workload_file: 工作负载（SQL语句集合）
    # config_file: 数据库Schema（表结构信息）

    # NOTE:读取配置文件中的workload_file,config_file和output_file
    if config.has_section('workload analyzer'):
        defaults.update(config['workload analyzer']) # 字典的update方法会覆盖defaults中的对应键值对

    # 解析命令行参数，默认值设为defaults
    parser = argparse.ArgumentParser()
    parser.add_argument('--workload_file', type=str, default=defaults['workload_file'])
    parser.add_argument('--config_file', type=str, default=defaults['config_file'])
    parser.add_argument('--output', type=str, default=defaults['output_file'])
    args = parser.parse_args()
    print(args) # 打印解析后的参数到终端

    # 设置print输出重定向到文件
    if args.output:
        sys.stdout = open(args.output, 'w')
    
    wp=WP2()
    # 解析数据库Schema（config_file），构建数据库表结构信息
    wp.parse_schema(args.config_file)
    # NOTE：没有将解析结果wp.dbs保存到特征文件—解析结果已足够作为特征，保持注释状态即可
    # print(wp.dbs.toStr())
    # print(wp.dbs.getTableByName('lineitem'))
    # print(type(wp.dbs.getTableByName('lineitem').col))

    # 解析工作负载SQL（workload_file），提取特征信息
    files=[args.workload_file] # 将工作负载文件路径添加到列表中
    for i in files:
        # print(i)
        wp.parse_workload(i)