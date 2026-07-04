# Domain Gate Retrieval Comparison

Compares `hybrid_dense` before and after deterministic record-level domain gating.

- Baseline potential false-positive notes: 3
- Domain-gated potential false-positive notes: 2
- Domain gate rejections recorded: 84
- API cost: $0; this evaluation is local retrieval only.

## 1. 他在不同時期如何看待追高？

### hybrid_dense_baseline

Episodes: `EP450, EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.713; domains ; relevance None)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 3.985; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 3.799; domains ; relevance None)
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (score 2.604; domains ; relevance None)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 2.327; domains ; relevance None)

Potential false positives:
- EP450-reasoning-007 may be lexically related but semantically weak for an investment query

### hybrid_dense_domain_gate

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 7.518; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 4.463; domains behavioral_investing, industry, investment, market, other; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.255; domains behavioral_investing, industry, investment, market; relevance high)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 2.606; domains industry, investment, market; relevance high)
- `EP57-reasoning-009` Market Timing Risk in Popular Stocks Despite Strong Fundamentals (score 2.182; domains behavioral_investing, industry, investment, market; relevance high)

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP450-reasoning-008` 言論審查應以刑事定罪為界，避免滑坡至取消文化 (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']

## 2. 面對市場不確定性時，他的做法是否一致？

### hybrid_dense_baseline

Episodes: `EP670, EP674`

- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 6.860; domains ; relevance None)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 5.093; domains ; relevance None)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.225; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.180; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.734; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP670, EP674`

- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 7.683; domains behavioral_investing, industry, investment, market, other; relevance high)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 5.704; domains industry, investment, market; relevance high)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.732; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.682; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.182; domains industry, investment, market; relevance high)

Domain gate rejections:
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']

## 3. 有哪些支持同一原則的不同案例？

### hybrid_dense_baseline

Episodes: `EP450`

- `EP450-reasoning-006` 券商服務權衡：國泰復委託低成本但缺乏客服支持 (score 4.779; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP450`

- `EP450-reasoning-006` 券商服務權衡：國泰復委託低成本但缺乏客服支持 (score 4.779; domains business, market, portfolio; relevance high)

## 4. 有沒有互相矛盾或有條件差異的觀點？

### hybrid_dense_baseline

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.356; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.226; domains ; relevance None)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 4.181; domains ; relevance None)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.158; domains ; relevance None)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 3.953; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.878; domains industry, investment, market; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.733; domains behavioral_investing, industry, investment, market; relevance high)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 4.683; domains behavioral_investing, industry, market; relevance high)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.657; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 4.427; domains behavioral_investing, industry, investment, market, portfolio; relevance high)

Domain gate rejections:
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']

## 5. 他對風險與踏空的態度有沒有改變？

### hybrid_dense_baseline

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-006` Wealth Effect Drives More Investing, Not Spending, in Bull Markets (score 6.644; domains ; relevance None)
- `EP57-reasoning-009` Market Timing Risk in Popular Stocks Despite Strong Fundamentals (score 6.570; domains ; relevance None)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.119; domains ; relevance None)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 6.036; domains ; relevance None)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 6.015; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-006` Wealth Effect Drives More Investing, Not Spending, in Bull Markets (score 7.441; domains behavioral_investing, industry, market; relevance high)
- `EP57-reasoning-009` Market Timing Risk in Popular Stocks Despite Strong Fundamentals (score 7.358; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.854; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 6.760; domains behavioral_investing, industry, market; relevance high)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 6.737; domains behavioral_investing, industry, investment, market, other, portfolio; relevance high)

Domain gate rejections:
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: behavioral_investing query with none investment relevance and domains ['other']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: behavioral_investing query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: behavioral_investing query with low investment relevance and domains ['business']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: behavioral_investing query with none investment relevance and domains ['other']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: behavioral_investing query with low investment relevance and domains ['career', 'personal']
- `EP450-reasoning-008` 言論審查應以刑事定罪為界，避免滑坡至取消文化 (other; none): rejected: behavioral_investing query with none investment relevance and domains ['other']

## 6. 哪些建議只適用於特定市場環境？

### hybrid_dense_baseline

Episodes: `EP450`

- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (score 3.342; domains ; relevance None)
- `EP450-reasoning-004` Apple財報平淡但回購強勁，AI伺服器與Vision Pro進展觀察中 (score 3.164; domains ; relevance None)
- `EP450-reasoning-003` AMD財報後下跌合理：市場過度高估AI營收 (score 2.988; domains ; relevance None)

Potential false positives:
- EP450-reasoning-007 may be lexically related but semantically weak for an investment query

### hybrid_dense_domain_gate

Episodes: `EP450`

- `EP450-reasoning-004` Apple財報平淡但回購強勁，AI伺服器與Vision Pro進展觀察中 (score 3.544; domains industry, market; relevance high)
- `EP450-reasoning-003` AMD財報後下跌合理：市場過度高估AI營收 (score 3.347; domains industry, market; relevance high)
- `EP450-reasoning-006` 券商服務權衡：國泰復委託低成本但缺乏客服支持 (score 2.167; domains business, market, portfolio; relevance high)

Potential false positives:
- EP450-reasoning-006 may be lexically related but semantically weak for an investment query

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: market_analysis query with low investment relevance and domains ['business']
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: market_analysis query with none investment relevance and domains ['personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: market_analysis query with none investment relevance and domains ['other']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: market_analysis query with low investment relevance and domains ['career', 'personal']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: market_analysis query with low investment relevance and domains ['business']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: market_analysis query with none investment relevance and domains ['other']

## 7. 他在台積電、AI、美股不同脈絡下的風險控管有什麼共通點？

### hybrid_dense_baseline

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.179; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 5.244; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.818; domains ; relevance None)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 4.551; domains ; relevance None)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 4.258; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.921; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 5.873; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 5.396; domains industry, investment, market; relevance high)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 5.097; domains behavioral_investing, industry, investment, market, other; relevance high)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 4.769; domains behavioral_investing, industry, investment, market, other, portfolio; relevance high)

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']

## 8. 現在適合追高買科技股嗎？

### hybrid_dense_baseline

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.644; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.881; domains ; relevance None)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 4.763; domains ; relevance None)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 3.813; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.730; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 7.441; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 5.467; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 5.334; domains behavioral_investing, industry, investment, market, other; relevance high)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 4.270; domains industry, investment, market; relevance high)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.177; domains industry, investment, market; relevance high)

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP450-reasoning-008` 言論審查應以刑事定罪為界，避免滑坡至取消文化 (other; none): rejected: risk_management query with none investment relevance and domains ['other']

## 9. 他在操作上最重視哪些條件？

### hybrid_dense_baseline

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.263; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 5.460; domains ; relevance None)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 5.250; domains ; relevance None)
- `EP670-reasoning-008` Personal finance during high-outflow periods (score 4.248; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.636; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 7.014; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 6.115; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 5.880; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-008` Personal finance during high-outflow periods (score 4.758; domains personal, portfolio; relevance medium)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.072; domains industry, investment, market; relevance high)

Domain gate rejections:
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']

## 10. 他面對市場不確定性時有固定做法嗎？

### hybrid_dense_baseline

Episodes: `EP670, EP674`

- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 6.861; domains ; relevance None)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 5.094; domains ; relevance None)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.226; domains ; relevance None)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.182; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.734; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP670, EP674`

- `EP674-reasoning-002` Market Correction Likely Driven by Institutional Rebalancing, Not Geopolitical or Inflation Fears (score 7.684; domains behavioral_investing, industry, investment, market, other; relevance high)
- `EP670-reasoning-004` Taiwan market correction likely complete (score 5.706; domains industry, investment, market; relevance high)
- `EP674-reasoning-001` Personal and Portfolio Success Driven by Market Beta and Disposition Stock Turnover (score 4.733; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.683; domains behavioral_investing, industry, investment, market; relevance high)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 4.182; domains industry, investment, market; relevance high)

Domain gate rejections:
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']

## 11. 他對追高與踏空的取捨是什麼？

### hybrid_dense_baseline

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 6.578; domains ; relevance None)
- `EP674-reasoning-006` Wealth Effect Drives More Investing, Not Spending, in Bull Markets (score 6.467; domains ; relevance None)
- `EP57-reasoning-009` Market Timing Risk in Popular Stocks Despite Strong Fundamentals (score 6.236; domains ; relevance None)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 5.633; domains ; relevance None)
- `EP57-reasoning-001` TSMC Valuation Based on P/E Range, Not Fixed Multiple (score 5.088; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP57, EP670, EP674`

- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 7.368; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP674-reasoning-006` Wealth Effect Drives More Investing, Not Spending, in Bull Markets (score 7.243; domains behavioral_investing, industry, market; relevance high)
- `EP57-reasoning-009` Market Timing Risk in Popular Stocks Despite Strong Fundamentals (score 6.984; domains behavioral_investing, industry, investment, market; relevance high)
- `EP670-reasoning-001` SpaceX as a multi-faceted company with narrative-driven valuation (score 6.309; domains behavioral_investing, industry, investment, market, other, portfolio; relevance high)
- `EP57-reasoning-001` TSMC Valuation Based on P/E Range, Not Fixed Multiple (score 5.698; domains behavioral_investing, industry, investment, market, portfolio; relevance high)

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: behavioral_investing query with low investment relevance and domains ['business']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: behavioral_investing query with low investment relevance and domains ['business']
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: behavioral_investing query with none investment relevance and domains ['personal']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: behavioral_investing query with low investment relevance and domains ['career', 'personal']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: behavioral_investing query with none investment relevance and domains ['other']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: behavioral_investing query with none investment relevance and domains ['other']

## 12. 哪些觀點只適用於特定市場環境？

### hybrid_dense_baseline

Episodes: `EP450`

- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (score 3.351; domains ; relevance None)
- `EP450-reasoning-004` Apple財報平淡但回購強勁，AI伺服器與Vision Pro進展觀察中 (score 3.171; domains ; relevance None)
- `EP450-reasoning-003` AMD財報後下跌合理：市場過度高估AI營收 (score 2.997; domains ; relevance None)

Potential false positives:
- EP450-reasoning-007 may be lexically related but semantically weak for an investment query

### hybrid_dense_domain_gate

Episodes: `EP450`

- `EP450-reasoning-004` Apple財報平淡但回購強勁，AI伺服器與Vision Pro進展觀察中 (score 3.551; domains industry, market; relevance high)
- `EP450-reasoning-003` AMD財報後下跌合理：市場過度高估AI營收 (score 3.357; domains industry, market; relevance high)
- `EP450-reasoning-006` 券商服務權衡：國泰復委託低成本但缺乏客服支持 (score 2.171; domains business, market, portfolio; relevance high)

Potential false positives:
- EP450-reasoning-006 may be lexically related but semantically weak for an investment query

Domain gate rejections:
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: market_analysis query with low investment relevance and domains ['business']
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: market_analysis query with none investment relevance and domains ['personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: market_analysis query with none investment relevance and domains ['other']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: market_analysis query with low investment relevance and domains ['career', 'personal']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: market_analysis query with low investment relevance and domains ['business']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: market_analysis query with none investment relevance and domains ['other']

## 13. 他有沒有前後不一致或有條件差異的看法？

### hybrid_dense_baseline

Episodes: `EP57, EP670, EP674`

- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.418; domains ; relevance None)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 4.398; domains ; relevance None)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 4.368; domains ; relevance None)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.456; domains ; relevance None)
- `EP57-reasoning-004` Prefer Low-Cost Index ETFs Over Actively Managed or Strategy-Based ETFs (score 3.219; domains ; relevance None)

### hybrid_dense_domain_gate

Episodes: `EP57, EP670, EP674`

- `EP670-reasoning-005` Regulatory risks from AI fear-mongering (score 4.948; domains behavioral_investing, industry, investment, market; relevance high)
- `EP57-reasoning-012` P/E Ratio as a Dynamic Range, Not Fixed Value (score 4.926; domains behavioral_investing, industry, market; relevance high)
- `EP674-reasoning-003` Break Below 60-Day Moving Average Signals Weakening Momentum and Higher Risk (score 4.892; domains behavioral_investing, industry, investment, market, portfolio; relevance high)
- `EP674-reasoning-004` Tension Between AI Component Supplier Profits and Tech Giant Spending (score 3.871; domains industry, investment, market; relevance high)
- `EP57-reasoning-004` Prefer Low-Cost Index ETFs Over Actively Managed or Strategy-Based ETFs (score 3.606; domains investment, market, portfolio; relevance high)

Domain gate rejections:
- `EP450-reasoning-005` 神經多樣性為光譜概念，自我調適優於藥物介入 (personal; none): rejected: risk_management query with none investment relevance and domains ['personal']
- `EP57-reasoning-007` Critique of Established Media Figures Resisting New Creators (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP57-reasoning-008` Defends Authentic Communication Style Against Calls for Censorship (other; none): rejected: risk_management query with none investment relevance and domains ['other']
- `EP674-reasoning-007` Prioritize Financial Stability Over Job Satisfaction in Early Career (career, personal; low): rejected: risk_management query with low investment relevance and domains ['career', 'personal']
- `EP674-reasoning-008` Simple Ownership Structures Prevent Business Complexity and Conflict (business; low): rejected: risk_management query with low investment relevance and domains ['business']
- `EP450-reasoning-007` 代購市場風險：價格競爭、仿冒與稅務稽查 (business; low): rejected: risk_management query with low investment relevance and domains ['business']
