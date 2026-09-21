import sqlite3

def risk_assessment(region):
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

    recent_rate = (deforms[-1] - deforms[-4]) / 3
    mid = len(deforms) // 2 
    first_rate = (deforms[mid] - deforms[0]) / mid
    second_rate = (deforms[-1] - deforms[mid]) / (len(deforms) - 1 - mid)
    last_step = deforms[-1] - deforms[-2]
    avg_step = (deforms[-1] - deforms[0]) / (len(deforms) - 1)

    score = 0
    if recent_rate > 10:
        score += 40
    if second_rate > first_rate:
        score += 30
    if last_step > avg_step * 3:
        score += 30

    if score <= 30:
        level = "低风险"
    elif score <= 60:
        level = "中风险"
    else:
        level = "较高风险"

    return (
    f"风险评分：{score} 分，等级：{level}。"
    f"指标明细：近3期平均月增量 {recent_rate:.1f}mm（阈值10），"
    f"前半段 {first_rate:.1f} vs 后半段 {second_rate:.1f} mm/期，"
    f"最近一期增量 {last_step:.1f}mm vs 历史平均 {avg_step:.1f}mm。")


print(risk_assessment("川西示范区"))
print(risk_assessment("不存在的区域"))

    