import sqlite3

def deformation_analysis(region):
    try:
        conn = sqlite3.connect("monitoring.db")
        cursor = conn.cursor()
        cursor.execute("SELECT deformation_mm FROM monitoring_series WHERE region = ? ORDER BY date",
            (region,),
        )
        rows = cursor.fetchall()
        conn.close()  
    except Exception as e:
        return f"查询出错:{e}"
    deforms = []
    for row in rows:
        deforms.append(row[0])

    if len(deforms) < 4 :
        return"数据不足"            

    max_v = deforms[0]
    for v in deforms:
        if v > max_v:
            max_v = v

    min_v = deforms[0]
    for v in deforms:
        if v < min_v:
            min_v = v

    total = 0
    count = 0
    for i in range(1,len(deforms)):
        step = deforms[i] - deforms[i-1]
        total = total + step
        count = count + 1

    s_total = 0                     
    for v in deforms:
        s_total = s_total + v
    mean_v = s_total / len(deforms)  # 平均形变量

    total_change = deforms[-1] - deforms[0]
    recent_rate = (deforms[-1] - deforms[-4]) / 3
    last_step = deforms[-1] - deforms[-2]
    avg_step = (deforms[-1] - deforms[0]) / (len(deforms) - 1)
    if avg_step*3 < last_step:
        abnormal = f"最近一期({last_step:.1f}mm)超过历史平均({avg_step:.1f}mm)的3倍，异常加速"
    else:
        abnormal = "未见异常加速"

    return (
        f"形变统计分析：\n"
        f"- 最大形变 {max_v}mm，最小 {min_v}mm，平均 {mean_v:.1f}mm，累计形变 {total_change:.1f}mm\n"
        f"- 近3期平均速率 {recent_rate:.1f}mm/期\n"
        f"- 异常提示：{abnormal}"
    )

print(deformation_analysis("川西示范区"))