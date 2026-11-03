"""按报表类型保存已通过人工与端到端核验的配平示例。"""

BALANCE_SHEET = """
<example>
<report>
<row item="非流动负债" parent=""></row>
<row item="长期借款" parent="非流动负债">60705569</row>
<row item="应付债券" parent="非流动负债">4997910</row>
<row item="租赁负债" parent="非流动负债">8620487</row>
<row item="递延所得税负债" parent="非流动负债">1617317</row>
<row item="其他非流动负债" parent="非流动负债">80798058</row>
<row item="非流动负债合计" parent="非流动负债">156739341</row>
</report>
<output>
{"checks": [{"sum": {"item": "非流动负债合计", "parent": "非流动负债"}, "add": [{"parent": "非流动负债", "items": ["长期借款", "应付债券", "租赁负债", "递延所得税负债", "其他非流动负债"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="非流動負債" parent="負債"></row>
<row item="優先股及按公允價值計入損益的其他金融負債" parent="非流動負債"></row>
<row item="非流動負債總額" parent="非流動負債">1095731</row>
<row item="流動負債" parent="負債"></row>
<row item="優先股及按公允價值計入損益的其他金融負債" parent="流動負債">14907698</row>
<row item="流動負債總額" parent="流動負債">16875641</row>
</report>
<output>
{"checks": [{"sum": {"item": "流動負債總額", "parent": "流動負債"}, "add": [{"parent": "流動負債", "items": ["優先股及按公允價值計入損益的其他金融負債"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="流動資產總值" parent="流動資產">24118150</row>
<row item="流動負債總額" parent="流動負債">16875641</row>
<row item="流動資產淨額" parent="">7242509</row>
</report>
<output>
{"checks": [{"sum": {"item": "流動資產淨額", "parent": ""}, "add": [{"parent": "流動資產", "items": ["流動資產總值"]}], "sub": [{"parent": "流動負債", "items": ["流動負債總額"]}]}]}
</output>
</example>
<example>
<report>
<row item="股东权益" parent=""></row>
<row item="股本" parent="股东权益">9117198</row>
<row item="其他权益工具" parent="股东权益">18733984</row>
<row item="资本公积" parent="股东权益">95276659</row>
<row item="库存股" parent="股东权益">3988271</row>
<row item="其他综合收益" parent="股东权益">3251842</row>
<row item="专项储备" parent="股东权益">31078</row>
<row item="盈余公积" parent="股东权益">7794688</row>
<row item="未分配利润" parent="股东权益">116057428</row>
<row item="归属于母公司股东权益合计" parent="股东权益">246274606</row>
</report>
<output>
{"checks": [{"sum": {"item": "归属于母公司股东权益合计", "parent": "股东权益"}, "add": [{"parent": "股东权益", "items": ["股本", "其他权益工具", "资本公积", "其他综合收益", "专项储备", "盈余公积", "未分配利润"]}], "sub": [{"parent": "股东权益", "items": ["库存股"]}]}]}
</output>
</example>
<example>
<report>
<row item="股东权益" parent=""></row>
<row item="股本" parent="股东权益">8557872087</row>
<row item="其他权益工具" parent="股东权益">335537354.91</row>
<row item="资本公积" parent="股东权益">3925995330.37</row>
<row item="库存股" parent="股东权益">530360387.47</row>
<row item="其他综合收益" parent="股东权益">457639577.31</row>
<row item="专项储备" parent="股东权益">596335318.86</row>
<row item="盈余公积" parent="股东权益">8299803096.95</row>
<row item="未分配利润" parent="股东权益">66249219099.30</row>
<row item="归属于母公司股东权益合计" parent="股东权益">87892041477.23</row>
</report>
<output>
{"checks": [{"sum": {"item": "归属于母公司股东权益合计", "parent": "股东权益"}, "add": [{"parent": "股东权益", "items": ["股本", "其他权益工具", "资本公积", "其他综合收益", "专项储备", "盈余公积", "未分配利润"]}], "sub": [{"parent": "股东权益", "items": ["库存股"]}]}]}
</output>
</example>
<example>
<report>
<row item="權益" parent=""></row>
<row item="歸屬於母公司股東的權益" parent="權益"></row>
<row item="股本" parent="歸屬於母公司股東的權益">242</row>
<row item="股份溢價" parent="歸屬於母公司股東的權益">45704716</row>
<row item="庫存股份" parent="歸屬於母公司股東的權益">-73124</row>
<row item="其他儲備" parent="歸屬於母公司股東的權益">268599</row>
<row item="累計虧損" parent="歸屬於母公司股東的權益">-33261189</row>
<row item="歸屬於母公司股東的權益合計" parent="歸屬於母公司股東的權益">12639244</row>
</report>
<output>
{"checks": [{"sum": {"item": "歸屬於母公司股東的權益合計", "parent": "歸屬於母公司股東的權益"}, "add": [{"parent": "歸屬於母公司股東的權益", "items": ["股本", "股份溢價", "庫存股份", "其他儲備", "累計虧損"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="非流动资产" parent=""></row>
<row item="长期应收款" parent="非流动资产">1535239923.48</row>
<row item="长期股权投资" parent="非流动资产">12222010879.85</row>
<row item="其他权益工具投资" parent="非流动资产">1396820410.69</row>
<row item="其他非流动金融资产" parent="非流动资产">306492679.28</row>
<row item="投资性房地产" parent="非流动资产">407747456.98</row>
<row item="固定资产" parent="非流动资产">29281846505.15</row>
<row item="在建工程" parent="非流动资产">5760111830.40</row>
<row item="使用权资产" parent="非流动资产">3132655393.05</row>
<row item="无形资产" parent="非流动资产">12234838813.07</row>
<row item="开发支出" parent="非流动资产">12212294264.98</row>
<row item="商誉" parent="非流动资产">27763359.77</row>
<row item="长期待摊费用" parent="非流动资产">890808129.79</row>
<row item="递延所得税资产" parent="非流动资产">6913115470.15</row>
<row item="其他非流动资产" parent="非流动资产">1318912154.90</row>
<row item="非流动资产合计" parent="非流动资产">87640657271.54</row>
</report>
<output>
{"checks": [{"sum": {"item": "非流动资产合计", "parent": "非流动资产"}, "add": [{"parent": "非流动资产", "items": ["长期应收款", "长期股权投资", "其他权益工具投资", "其他非流动金融资产", "投资性房地产", "固定资产", "在建工程", "使用权资产", "无形资产", "开发支出", "商誉", "长期待摊费用", "递延所得税资产", "其他非流动资产"]}], "sub": []}]}
</output>
</example>
"""

INCOME_STATEMENT = """
<example>
<report>
<row item="营业总收入" parent="">120</row>
<row item="营业总成本" parent="">80</row>
<row item="财务费用" parent="">-10</row>
<row item="信用减值损失" parent="">-5</row>
<row item="营业利润" parent="">43</row>
</report>
<output>
{"checks": [{"sum": {"item": "营业利润", "parent": ""}, "add": [{"parent": "", "items": ["营业总收入", "信用减值损失"]}], "sub": [{"parent": "", "items": ["营业总成本", "财务费用"]}]}]}
</output>
</example>
<example>
<report>
<row item="利润总额" parent="">39753049</row>
<row item="所得税费用" parent="">5992291</row>
<row item="净利润" parent="">33760758</row>
</report>
<output>
{"checks": [{"sum": {"item": "净利润", "parent": ""}, "add": [{"parent": "", "items": ["利润总额"]}], "sub": [{"parent": "", "items": ["所得税费用"]}]}]}
</output>
</example>
<example>
<report>
<row item="营业收入" parent="">803964958</row>
<row item="营业成本" parent="">661305161</row>
<row item="税金及附加" parent="">13580544</row>
<row item="销售费用" parent="">26184568</row>
<row item="管理费用" parent="">20199944</row>
<row item="研发费用" parent="">57978105</row>
<row item="财务费用" parent="">-649350</row>
<row item="其他收益" parent="">13800300</row>
<row item="投资收益" parent="">2856608</row>
<row item="公允价值变动收益" parent="">364238</row>
<row item="信用减值损失" parent="">-407137</row>
<row item="资产减值损失" parent="">-1823082</row>
<row item="资产处置收益" parent="">27796</row>
<row item="营业利润" parent="">40184709</row>
</report>
<output>
{"checks": [{"sum": {"item": "营业利润", "parent": ""}, "add": [{"parent": "", "items": ["营业收入", "其他收益", "投资收益", "公允价值变动收益", "信用减值损失", "资产减值损失", "资产处置收益"]}], "sub": [{"parent": "", "items": ["营业成本", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用"]}]}]}
</output>
</example>
<example>
<report>
<row item="毛利" parent="">2425680</row>
<row item="研發開支" parent="">-5153708</row>
<row item="行政開支" parent="">-725964</row>
<row item="銷售及營銷開支" parent="">-631924</row>
<row item="金融資產減值虧損淨額" parent="">-9575</row>
<row item="其他收入" parent="">335183</row>
<row item="其他收益淨額" parent="">421517</row>
<row item="經營虧損" parent="">-3338791</row>
</report>
<output>
{"checks": [{"sum": {"item": "經營虧損", "parent": ""}, "add": [{"parent": "", "items": ["毛利", "研發開支", "行政開支", "銷售及營銷開支", "金融資產減值虧損淨額", "其他收入", "其他收益淨額"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="財務收入" parent="財務收入淨額">403938</row>
<row item="財務成本" parent="財務收入淨額">-14089</row>
<row item="財務收入淨額" parent="">389849</row>
</report>
<output>
{"checks": [{"sum": {"item": "財務收入淨額", "parent": ""}, "add": [{"parent": "財務收入淨額", "items": ["財務收入", "財務成本"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="年內(虧損)/利潤" parent="">-10469366</row>
<row item="以下各方應佔(虧損)/利潤" parent="年內(虧損)/利潤"></row>
<row item="母公司股東" parent="以下各方應佔(虧損)/利潤">-10469012</row>
<row item="非控股權益" parent="以下各方應佔(虧損)/利潤">-354</row>
</report>
<output>
{"checks": [{"sum": {"item": "年內(虧損)/利潤", "parent": ""}, "add": [{"parent": "以下各方應佔(虧損)/利潤", "items": ["母公司股東", "非控股權益"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="除所得稅前(虧損)/利潤" parent="">-10466047</row>
<row item="所得稅開支" parent="">-3319</row>
<row item="年內(虧損)/利潤" parent="">-10469366</row>
<row item="以下各方應佔(虧損)/利潤" parent="年內(虧損)/利潤"></row>
<row item="母公司股東" parent="以下各方應佔(虧損)/利潤">-10469012</row>
<row item="非控股權益" parent="以下各方應佔(虧損)/利潤">-354</row>
</report>
<output>
{"checks": [
{"sum": {"item": "年內(虧損)/利潤", "parent": ""}, "add": [{"parent": "", "items": ["除所得稅前(虧損)/利潤", "所得稅開支"]}], "sub": []},
{"sum": {"item": "年內(虧損)/利潤", "parent": ""}, "add": [{"parent": "以下各方應佔(虧損)/利潤", "items": ["母公司股東", "非控股權益"]}], "sub": []}
]}
</output>
</example>
<example>
<report>
<row item="财务费用" parent="">-649350</row>
<row item="利息费用" parent="财务费用">2551657</row>
<row item="利息收入" parent="财务费用">2436953</row>
</report>
<output>{"checks": []}</output>
</example>
<example>
<report>
<row item="财务费用" parent="">10961179.66</row>
<row item="利息费用" parent="财务费用">19021242.67</row>
<row item="利息收入" parent="财务费用">15244525.38</row>
</report>
<output>{"checks": []}</output>
</example>
<example>
<report>
<row item="財務費用" parent="">-1969734697.67</row>
<row item="利息費用" parent="財務費用">735538728.32</row>
<row item="利息收入" parent="財務費用">1093994266.48</row>
</report>
<output>{"checks": []}</output>
</example>
<example>
<report>
<row item="净利润" parent="">33760758</row>
<row item="其他综合收益" parent="">1793151</row>
<row item="综合收益总额" parent="">35553909</row>
<row item="归属于母公司股东的综合收益总额" parent="综合收益总额">34430248</row>
<row item="归属于少数股东的综合收益总额" parent="综合收益总额">1123661</row>
</report>
<output>
{"checks": [
{"sum": {"item": "综合收益总额", "parent": ""}, "add": [{"parent": "", "items": ["净利润", "其他综合收益"]}], "sub": []},
{"sum": {"item": "综合收益总额", "parent": ""}, "add": [{"parent": "综合收益总额", "items": ["归属于母公司股东的综合收益总额", "归属于少数股东的综合收益总额"]}], "sub": []}
]}
</output>
</example>
<example>
<report>
<row item="营业利润(亏损以“-”号填列)" parent="">2061390034.78</row>
<row item="营业外收入" parent="">2310146.30</row>
<row item="营业外支出" parent="">4318610.73</row>
<row item="利润总额(亏损总额以“-”号填列)" parent="">2059381570.35</row>
<row item="所得税费用" parent="">-894283.44</row>
<row item="净利润(净亏损以“-”号填列)" parent="">2060495853.79</row>
</report>
<output>
{"checks": [
{"sum": {"item": "利润总额(亏损总额以“-”号填列)", "parent": ""}, "add": [{"parent": "", "items": ["营业利润(亏损以“-”号填列)", "营业外收入"]}], "sub": [{"parent": "", "items": ["营业外支出"]}]},
{"sum": {"item": "净利润(净亏损以“-”号填列)", "parent": ""}, "add": [{"parent": "", "items": ["利润总额(亏损总额以“-”号填列)"]}], "sub": [{"parent": "", "items": ["所得税费用"]}]}
]}
</output>
</example>
<example>
<report>
<row item="營業總收入" parent="">222824238516.25</row>
<row item="營業總成本" parent="">215587879531.62</row>
<row item="財務費用" parent="營業總成本">-1969734697.67</row>
<row item="其他收益" parent="">4042097770.22</row>
<row item="投資收益" parent="">573928380.94</row>
<row item="公允價值變動收益" parent="">166730636.22</row>
<row item="信用減值損失" parent="">-13254999.29</row>
<row item="資產減值損失" parent="">-576225684.91</row>
<row item="資產處置收益" parent="">104030316.88</row>
<row item="營業利潤" parent="">11533665404.69</row>
</report>
<output>
{"checks": [{"sum": {"item": "營業利潤", "parent": ""}, "add": [{"parent": "", "items": ["營業總收入", "其他收益", "投資收益", "公允價值變動收益", "信用減值損失", "資產減值損失", "資產處置收益"]}], "sub": [{"parent": "", "items": ["營業總成本"]}]}]}
</output>
</example>
<example>
<report>
<row item="其他综合收益" parent="">1793151</row>
<row item="不能重分类进损益的其他综合收益" parent="其他综合收益"></row>
<row item="不能重分类进损益的其他综合收益合计" parent="不能重分类进损益的其他综合收益">1267339</row>
<row item="将重分类进损益的其他综合收益" parent="其他综合收益"></row>
<row item="将重分类进损益的其他综合收益合计" parent="将重分类进损益的其他综合收益">543887</row>
<row item="归属于少数股东的其他综合收益的税后净额" parent="其他综合收益">-18075</row>
</report>
<output>
{"checks": [{"sum": {"item": "其他综合收益", "parent": ""}, "add": [{"parent": "不能重分类进损益的其他综合收益", "items": ["不能重分类进损益的其他综合收益合计"]}, {"parent": "将重分类进损益的其他综合收益", "items": ["将重分类进损益的其他综合收益合计"]}, {"parent": "其他综合收益", "items": ["归属于少数股东的其他综合收益的税后净额"]}], "sub": []}]}
</output>
</example>
"""

CASH_FLOW = """
<example>
<report>
<row item="筹资活动产生的现金流量" parent=""></row>
<row item="偿还债务支付的现金" parent="筹资活动产生的现金流量">100</row>
<row item="分配股利、利润或偿付利息支付的现金" parent="筹资活动产生的现金流量">40</row>
<row item="子公司支付给少数股东的股利、利润" parent="分配股利、利润或偿付利息支付的现金">10</row>
<row item="支付给其他权益所有者利息" parent="分配股利、利润或偿付利息支付的现金">5</row>
<row item="筹资活动现金流出小计" parent="筹资活动产生的现金流量">140</row>
</report>
<output>
{"checks": [{"sum": {"item": "筹资活动现金流出小计", "parent": "筹资活动产生的现金流量"}, "add": [{"parent": "筹资活动产生的现金流量", "items": ["偿还债务支付的现金", "分配股利、利润或偿付利息支付的现金"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="經營活動產生的(現金流出)/現金流入淨額" parent="經營活動產生的現金流量">-2106052</row>
<row item="投資活動產生的現金流出淨額" parent="投資活動產生的現金流量">-4929915</row>
<row item="融資活動產生的現金流入淨額" parent="融資活動產生的現金流量">12110892</row>
<row item="現金及現金等價物增加淨額" parent="">5074925</row>
<row item="年初現金及現金等價物" parent="">15370925</row>
<row item="匯率變動對現金及現金等價物的影響" parent="">-257780</row>
<row item="年末現金及現金等價物" parent="">20188070</row>
</report>
<output>
{"checks": [
{"sum": {"item": "現金及現金等價物增加淨額", "parent": ""}, "add": [{"parent": "經營活動產生的現金流量", "items": ["經營活動產生的(現金流出)/現金流入淨額"]}, {"parent": "投資活動產生的現金流量", "items": ["投資活動產生的現金流出淨額"]}, {"parent": "融資活動產生的現金流量", "items": ["融資活動產生的現金流入淨額"]}], "sub": []},
{"sum": {"item": "年末現金及現金等價物", "parent": ""}, "add": [{"parent": "", "items": ["現金及現金等價物增加淨額", "年初現金及現金等價物", "匯率變動對現金及現金等價物的影響"]}], "sub": []}
]}
</output>
</example>
<example>
<report>
<row item="經營活動產生的現金流量" parent=""></row>
<row item="經營活動所用現金" parent="經營活動產生的現金流量">-2505052</row>
<row item="已收利息" parent="經營活動產生的現金流量">399608</row>
<row item="已付所得稅" parent="經營活動產生的現金流量">-608</row>
<row item="經營活動產生的(現金流出)/現金流入淨額" parent="經營活動產生的現金流量">-2106052</row>
</report>
<output>
{"checks": [{"sum": {"item": "經營活動產生的(現金流出)/現金流入淨額", "parent": "經營活動產生的現金流量"}, "add": [{"parent": "經營活動產生的現金流量", "items": ["經營活動所用現金", "已收利息", "已付所得稅"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="经营活动产生的现金流量" parent=""></row>
<row item="经营活动现金流入小计" parent="经营活动产生的现金流量">100</row>
<row item="经营活动现金流出小计" parent="经营活动产生的现金流量">70</row>
<row item="经营活动产生的现金流量净额" parent="经营活动产生的现金流量">30</row>
</report>
<output>
{"checks": [{"sum": {"item": "经营活动产生的现金流量净额", "parent": "经营活动产生的现金流量"}, "add": [{"parent": "经营活动产生的现金流量", "items": ["经营活动现金流入小计"]}], "sub": [{"parent": "经营活动产生的现金流量", "items": ["经营活动现金流出小计"]}]}]}
</output>
</example>
<example>
<report>
<row item="筹资活动产生的现金流量" parent=""></row>
<row item="偿还债务支付的现金" parent="筹资活动产生的现金流量">69220472</row>
<row item="分配股利、利润或偿付利息支付的现金" parent="筹资活动产生的现金流量">14125279</row>
<row item="子公司支付给少数股东的股利、利润" parent="分配股利、利润或偿付利息支付的现金">438210</row>
<row item="支付给其他权益所有者利息" parent="分配股利、利润或偿付利息支付的现金">546796</row>
<row item="其他权益工具持有者赎回支付的现金" parent="筹资活动产生的现金流量">28000000</row>
<row item="支付其他与筹资活动有关的现金" parent="筹资活动产生的现金流量">10306261</row>
<row item="筹资活动现金流出小计" parent="筹资活动产生的现金流量">121652012</row>
</report>
<output>
{"checks": [{"sum": {"item": "筹资活动现金流出小计", "parent": "筹资活动产生的现金流量"}, "add": [{"parent": "筹资活动产生的现金流量", "items": ["偿还债务支付的现金", "分配股利、利润或偿付利息支付的现金", "其他权益工具持有者赎回支付的现金", "支付其他与筹资活动有关的现金"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="筹资活动产生的现金流量" parent=""></row>
<row item="筹资活动现金流入小计" parent="筹资活动产生的现金流量">226265882</row>
<row item="筹资活动现金流出小计" parent="筹资活动产生的现金流量">121652012</row>
<row item="筹资活动产生/(使用)的现金流量净额" parent="筹资活动产生的现金流量">104613870</row>
</report>
<output>
{"checks": [{"sum": {"item": "筹资活动产生/(使用)的现金流量净额", "parent": "筹资活动产生的现金流量"}, "add": [{"parent": "筹资活动产生的现金流量", "items": ["筹资活动现金流入小计"]}], "sub": [{"parent": "筹资活动产生的现金流量", "items": ["筹资活动现金流出小计"]}]}]}
</output>
</example>
<example>
<report>
<row item="筹资活动产生的现金流量" parent=""></row>
<row item="筹资活动现金流入小计" parent="筹资活动产生的现金流量">226265882</row>
<row item="偿还债务支付的现金" parent="筹资活动产生的现金流量">69220472</row>
<row item="分配股利、利润或偿付利息支付的现金" parent="筹资活动产生的现金流量">14125279</row>
<row item="子公司支付给少数股东的股利、利润" parent="分配股利、利润或偿付利息支付的现金">438210</row>
<row item="支付给其他权益所有者利息" parent="分配股利、利润或偿付利息支付的现金">546796</row>
<row item="其他权益工具持有者赎回支付的现金" parent="筹资活动产生的现金流量">28000000</row>
<row item="支付其他与筹资活动有关的现金" parent="筹资活动产生的现金流量">10306261</row>
<row item="筹资活动现金流出小计" parent="筹资活动产生的现金流量">121652012</row>
<row item="筹资活动产生/(使用)的现金流量净额" parent="筹资活动产生的现金流量">104613870</row>
</report>
<output>
{"checks": [
{"sum": {"item": "筹资活动现金流出小计", "parent": "筹资活动产生的现金流量"}, "add": [{"parent": "筹资活动产生的现金流量", "items": ["偿还债务支付的现金", "分配股利、利润或偿付利息支付的现金", "其他权益工具持有者赎回支付的现金", "支付其他与筹资活动有关的现金"]}], "sub": []},
{"sum": {"item": "筹资活动产生/(使用)的现金流量净额", "parent": "筹资活动产生的现金流量"}, "add": [{"parent": "筹资活动产生的现金流量", "items": ["筹资活动现金流入小计"]}], "sub": [{"parent": "筹资活动产生的现金流量", "items": ["筹资活动现金流出小计"]}]}
]}
</output>
</example>
<example>
<report>
<row item="经营活动产生的现金流量净额" parent="">10896126160.03</row>
<row item="投资活动产生的现金流量净额" parent="">-11008154953.83</row>
<row item="筹资活动产生的现金流量净额" parent="">-177925789.80</row>
<row item="汇率变动对现金及现金等价物的影响" parent="">-44373.50</row>
<row item="现金及现金等价物净增加额" parent="">-1042784853.85</row>
</report>
<output>
{"checks": [{"sum": {"item": "现金及现金等价物净增加额", "parent": ""}, "add": [{"parent": "", "items": ["经营活动产生的现金流量净额", "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额", "汇率变动对现金及现金等价物的影响"]}], "sub": []}]}
</output>
</example>
<example>
<report>
<row item="經營活動產生的現金流量淨額" parent="">100</row>
<row item="投資活動產生的現金流量淨額" parent="">-30</row>
<row item="融資活動產生的現金流量淨額" parent="">20</row>
<row item="現金及現金等價物增加淨額" parent="">90</row>
<row item="年初現金及現金等價物" parent="">1000</row>
<row item="匯率變動對現金及現金等價物的影響" parent="">5</row>
<row item="年末現金及現金等價物" parent="">1095</row>
</report>
<output>
{"checks": [
{"sum": {"item": "現金及現金等價物增加淨額", "parent": ""}, "add": [{"parent": "", "items": ["經營活動產生的現金流量淨額", "投資活動產生的現金流量淨額", "融資活動產生的現金流量淨額"]}], "sub": []},
{"sum": {"item": "年末現金及現金等價物", "parent": ""}, "add": [{"parent": "", "items": ["現金及現金等價物增加淨額", "年初現金及現金等價物", "匯率變動對現金及現金等價物的影響"]}], "sub": []}
]}
</output>
</example>
<example>
<report>
<row item="融資活動產生的現金流量" parent=""></row>
<row item="發行普通股所得款項" parent="融資活動產生的現金流量">10062606</row>
<row item="發行優先股及其他金融負債所得款項" parent="融資活動產生的現金流量">2023776</row>
<row item="股份購回付款以結算僱員稅項" parent="融資活動產生的現金流量">-73124</row>
<row item="非控股權益股東注資" parent="融資活動產生的現金流量">None</row>
<row item="行使購股權所得款項" parent="融資活動產生的現金流量">65919</row>
<row item="發行優先股負債的交易成本支付" parent="融資活動產生的現金流量">-4264</row>
<row item="非經常性增資開支" parent="融資活動產生的現金流量">-616</row>
<row item="租賃付款的本金部分" parent="融資活動產生的現金流量">-77150</row>
<row item="租賃付款的利息部分" parent="融資活動產生的現金流量">-6981</row>
<row item="借款所得款項" parent="融資活動產生的現金流量">120726</row>
<row item="融資活動產生的現金流入淨額" parent="融資活動產生的現金流量">12110892</row>
</report>
<output>
{"checks": [{"sum": {"item": "融資活動產生的現金流入淨額", "parent": "融資活動產生的現金流量"}, "add": [{"parent": "融資活動產生的現金流量", "items": ["發行普通股所得款項", "發行優先股及其他金融負債所得款項", "股份購回付款以結算僱員稅項", "非控股權益股東注資", "行使購股權所得款項", "發行優先股負債的交易成本支付", "非經常性增資開支", "租賃付款的本金部分", "租賃付款的利息部分", "借款所得款項"]}], "sub": []}]}
</output>
</example>
"""
