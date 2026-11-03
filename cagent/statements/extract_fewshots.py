"""按报表类型保存 HTML 到极简 rows 的提取示例。"""

BALANCE_SHEET = """
<example>
<input><table>
<tr><td colspan="4">流动资产</td></tr>
<tr><td>货币资金</td><td>75,424,747</td><td>102,738,734</td></tr>
<tr><td>衍生金融资产</td><td>-</td><td>35,093</td></tr>
<tr><td>流动资产合计</td><td>371,467,558</td><td>370,572,244</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "流动资产", "value": "", "parent": ""},
  {"item": "货币资金", "value": "75,424,747", "parent": "流动资产"},
  {"item": "衍生金融资产", "value": "", "parent": "流动资产"},
  {"item": "流动资产合计", "value": "371,467,558", "parent": "流动资产"}
]}
</output>
</example>
<example>
<input><table>
<tr><td>股本</td><td>9,117,198</td></tr>
<tr><td>其他权益工具</td><td>18,733,984</td></tr>
<tr><td>其中：永续债</td><td>18,733,984</td></tr>
<tr><td>减：库存股</td><td>3,988,271</td></tr>
<tr><td>归属于母公司股东权益合计</td><td>246,274,606</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "股本", "value": "9,117,198", "parent": "股东权益"},
  {"item": "其他权益工具", "value": "18,733,984", "parent": "股东权益"},
  {"item": "永续债", "value": "18,733,984", "parent": "其他权益工具"},
  {"item": "库存股", "value": "3,988,271", "parent": "股东权益"},
  {"item": "归属于母公司股东权益合计", "value": "246,274,606", "parent": "股东权益"}
]}
</output>
</example>
<example>
<input><table>
<tr><td>歸屬於母公司股東的權益</td><td></td></tr>
<tr><td>股本</td><td>242</td></tr>
<tr><td>股份溢價</td><td>45,704,716</td></tr>
<tr><td>庫存股份</td><td>(73,124)</td></tr>
<tr><td>其他儲備</td><td>268,599</td></tr>
<tr><td>累計虧損</td><td>(33,261,189)</td></tr>
<tr><td></td><td>12,639,244</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "歸屬於母公司股東的權益", "value": "", "parent": "權益"},
  {"item": "股本", "value": "242", "parent": "歸屬於母公司股東的權益"},
  {"item": "股份溢價", "value": "45,704,716", "parent": "歸屬於母公司股東的權益"},
  {"item": "庫存股份", "value": "(73,124)", "parent": "歸屬於母公司股東的權益"},
  {"item": "其他儲備", "value": "268,599", "parent": "歸屬於母公司股東的權益"},
  {"item": "累計虧損", "value": "(33,261,189)", "parent": "歸屬於母公司股東的權益"},
  {"item": "歸屬於母公司股東的權益合計", "value": "12,639,244", "parent": "歸屬於母公司股東的權益"}
]}
</output>
</example>
"""

INCOME_STATEMENT = """
<example>
<input><table>
<tr><td>一、营业收入</td><td>803,964,958</td></tr>
<tr><td>减:营业成本</td><td>661,305,161</td></tr>
<tr><td>财务费用</td><td>(649,350)</td></tr>
<tr><td>其中:利息费用</td><td>2,551,657</td></tr>
<tr><td>利息收入</td><td>2,436,953</td></tr>
<tr><td>二、营业利润</td><td>40,184,709</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "营业收入", "value": "803,964,958", "parent": ""},
  {"item": "营业成本", "value": "661,305,161", "parent": ""},
  {"item": "财务费用", "value": "(649,350)", "parent": ""},
  {"item": "利息费用", "value": "2,551,657", "parent": "财务费用"},
  {"item": "利息收入", "value": "2,436,953", "parent": "财务费用"},
  {"item": "营业利润", "value": "40,184,709", "parent": ""}
]}
</output>
</example>
<example>
<input><table>
<tr><td>財務收入</td><td>403,938</td></tr>
<tr><td>財務成本</td><td>(14,089)</td></tr>
<tr><td>財務收入淨額</td><td>389,849</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "財務收入", "value": "403,938", "parent": "財務收入淨額"},
  {"item": "財務成本", "value": "(14,089)", "parent": "財務收入淨額"},
  {"item": "財務收入淨額", "value": "389,849", "parent": ""}
]}
</output>
</example>
"""

CASH_FLOW = """
<example>
<input><table>
<tr><td>三、筹资活动产生的现金流量:</td><td></td></tr>
<tr><td>偿还债务支付的现金</td><td>69,220,472</td></tr>
<tr><td>分配股利、利润或偿付利息支付的现金</td><td>14,125,279</td></tr>
<tr><td>其中:子公司支付给少数股东的股利、利润</td><td>438,210</td></tr>
<tr><td>支付给其他权益所有者利息</td><td>546,796</td></tr>
<tr><td>其他权益工具持有者赎回支付的现金</td><td>28,000,000</td></tr>
<tr><td>支付其他与筹资活动有关的现金</td><td>10,306,261</td></tr>
<tr><td>筹资活动现金流出小计</td><td>121,652,012</td></tr>
<tr><td>筹资活动产生/(使用)的现金流量净额</td><td>104,613,870</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "筹资活动产生的现金流量", "value": "", "parent": ""},
  {"item": "偿还债务支付的现金", "value": "69,220,472", "parent": "筹资活动产生的现金流量"},
  {"item": "分配股利、利润或偿付利息支付的现金", "value": "14,125,279", "parent": "筹资活动产生的现金流量"},
  {"item": "子公司支付给少数股东的股利、利润", "value": "438,210", "parent": "分配股利、利润或偿付利息支付的现金"},
  {"item": "支付给其他权益所有者利息", "value": "546,796", "parent": "分配股利、利润或偿付利息支付的现金"},
  {"item": "其他权益工具持有者赎回支付的现金", "value": "28,000,000", "parent": "筹资活动产生的现金流量"},
  {"item": "支付其他与筹资活动有关的现金", "value": "10,306,261", "parent": "筹资活动产生的现金流量"},
  {"item": "筹资活动现金流出小计", "value": "121,652,012", "parent": "筹资活动产生的现金流量"},
  {"item": "筹资活动产生/(使用)的现金流量净额", "value": "104,613,870", "parent": "筹资活动产生的现金流量"}
]}
</output>
</example>
<example>
<input><table>
<tr><td>經營活動所用現金</td><td>(2,505,052)</td></tr>
<tr><td>已收利息</td><td>399,608</td></tr>
<tr><td>已付所得稅</td><td>(608)</td></tr>
<tr><td>經營活動產生的(現金流出)/現金流入淨額</td><td>(2,106,052)</td></tr>
</table></input>
<output>
{"rows": [
  {"item": "經營活動所用現金", "value": "(2,505,052)", "parent": "經營活動產生的現金流量"},
  {"item": "已收利息", "value": "399,608", "parent": "經營活動產生的現金流量"},
  {"item": "已付所得稅", "value": "(608)", "parent": "經營活動產生的現金流量"},
  {"item": "經營活動產生的(現金流出)/現金流入淨額", "value": "(2,106,052)", "parent": "經營活動產生的現金流量"}
]}
</output>
</example>
"""
