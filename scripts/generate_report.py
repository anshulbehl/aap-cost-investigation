#!/usr/bin/env python3
"""Generate an HTML cost investigation report from a JSON data file.

Usage:
    python3 scripts/generate_report.py reports/data.json
    python3 scripts/generate_report.py reports/data.json -o reports/report.html

The JSON data file should contain artifacts collected from the 6 AAP job
templates. Claude writes this small JSON file; this script does the heavy
HTML rendering so Claude doesn't burn tokens on markup.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def fmt_cost(value):
    """Format a number as a dollar cost string."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$0.00"
    return f"${v:,.2f}"


def fmt_pct(value):
    """Format a number as a percentage string."""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def compliance_bar_width(rate):
    """Return CSS width for the compliance bar (min 5% so label is visible)."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        r = 0
    return max(5, r)


def compliance_bar_color(rate):
    try:
        r = float(rate)
    except (TypeError, ValueError):
        r = 0
    if r >= 80:
        return "#3E8635"
    if r >= 50:
        return "#F0AB00"
    return "#EE0000"


def build_summary_cards(data):
    savings = data.get("savings", {})
    idle = data.get("idle", {})
    compliance = data.get("compliance", {})
    ebs = data.get("ebs_waste", {})
    eip = data.get("eip_waste", {})

    running_count = len(savings.get("cost_details", []))
    stopped_count = int(savings.get("stopped_count", 0))
    total = running_count + stopped_count
    monthly_spend = float(savings.get("monthly_spend", 0))
    idle_count = int(idle.get("idle_count", 0))
    compliance_rate = float(compliance.get("compliance_rate", 0))
    rightsizing = float(savings.get("rightsizing_savings", 0))
    ebs_total = float(ebs.get("unattached_cost", 0)) + float(ebs.get("stopped_cost", 0))
    eip_total = float(eip.get("monthly_waste", 0))

    cards = []

    cards.append(
        f'<div class="card">'
        f'<div class="card-label">Total Instances</div>'
        f'<div class="card-value">{total}</div>'
        f'<div class="card-detail">{running_count} running / {stopped_count} stopped</div>'
        f"</div>"
    )

    cards.append(
        f'<div class="card alert">'
        f'<div class="card-label">Monthly Spend</div>'
        f'<div class="card-value">{fmt_cost(monthly_spend)}</div>'
        f'<div class="card-detail">On-demand estimate (running)</div>'
        f"</div>"
    )

    if idle_count > 0:
        idle_pct = (idle_count / running_count * 100) if running_count > 0 else 0
        cards.append(
            f'<div class="card alert">'
            f'<div class="card-label">Idle Instances</div>'
            f'<div class="card-value">{idle_count}</div>'
            f'<div class="card-detail">{idle_pct:.0f}% of running are idle</div>'
            f"</div>"
        )

    cls = "alert" if compliance_rate < 50 else ("success" if compliance_rate >= 80 else "card")
    cards.append(
        f'<div class="card {cls}">'
        f'<div class="card-label">Tag Compliance</div>'
        f'<div class="card-value">{fmt_pct(compliance_rate)}</div>'
        f'<div class="card-detail">{len(compliance.get("non_compliant_instances", []))} non-compliant</div>'
        f"</div>"
    )

    if rightsizing > 0:
        cards.append(
            f'<div class="card success">'
            f'<div class="card-label">Rightsizing Savings</div>'
            f'<div class="card-value cost-savings">{fmt_cost(rightsizing)}/mo</div>'
            f'<div class="card-detail">If all downsized</div>'
            f"</div>"
        )

    if ebs_total > 0:
        unattached_count = len(ebs.get("unattached_volumes", []))
        stopped_vol_count = len(ebs.get("stopped_volumes", []))
        detail_parts = []
        if unattached_count:
            detail_parts.append(f"{unattached_count} unattached")
        if stopped_vol_count:
            detail_parts.append(f"{stopped_vol_count} on stopped instances")
        cards.append(
            f'<div class="card alert">'
            f'<div class="card-label">EBS Waste</div>'
            f'<div class="card-value">{fmt_cost(ebs_total)}/mo</div>'
            f'<div class="card-detail">{", ".join(detail_parts)} volumes</div>'
            f"</div>"
        )

    if eip_total > 0:
        eip_count = int(eip.get("unattached_count", 0))
        cards.append(
            f'<div class="card alert">'
            f'<div class="card-label">EIP Waste</div>'
            f'<div class="card-value">{fmt_cost(eip_total)}/mo</div>'
            f'<div class="card-detail">{eip_count} unattached Elastic IPs</div>'
            f"</div>"
        )

    return "\n    ".join(cards)


def build_cost_table(data):
    details = data.get("savings", {}).get("cost_details", [])
    if not details:
        return '<div class="section-empty">No running instances found (savings report may have failed).</div>'

    sorted_details = sorted(details, key=lambda x: float(x.get("monthly_cost", 0)), reverse=True)

    rows = []
    for inst in sorted_details:
        mc = float(inst.get("monthly_cost", 0))
        cost_cls = " cost-high" if mc >= 200 else ""
        ds = inst.get("downsize_to", "N/A")
        ds_savings = float(inst.get("downsize_savings", 0))
        savings_cell = f'<td class="text-right cost-savings">{fmt_cost(ds_savings)}</td>' if ds_savings > 0 else '<td class="text-right">--</td>'

        rows.append(
            f"<tr>"
            f'<td class="mono">{inst["instance_id"]}</td>'
            f'<td>{inst.get("name", "UNTAGGED")}</td>'
            f'<td class="mono">{inst["instance_type"]}</td>'
            f'<td class="text-right">${float(inst.get("hourly_cost", 0)):.4f}</td>'
            f'<td class="text-right cost{cost_cls}">{fmt_cost(mc)}</td>'
            f'<td class="mono">{ds}</td>'
            f"{savings_cell}"
            f"</tr>"
        )

    total_spend = sum(float(i.get("monthly_cost", 0)) for i in details)
    total_savings = sum(float(i.get("downsize_savings", 0)) for i in details)

    return (
        "<table><thead><tr>"
        '<th>Instance ID</th><th>Name</th><th>Type</th>'
        '<th class="text-right">Hourly ($)</th><th class="text-right">Monthly ($)</th>'
        '<th>Downsize To</th><th class="text-right">Savings ($)</th>'
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody><tfoot>"
        f'<tr style="font-weight:700; background:#f0f5ff;">'
        f"<td colspan=\"4\">Total ({len(details)} running instances)</td>"
        f'<td class="text-right cost">{fmt_cost(total_spend)}</td>'
        f"<td></td>"
        f'<td class="text-right cost-savings">{fmt_cost(total_savings)}</td>'
        f"</tr></tfoot></table>"
    )


def build_idle_table(data):
    idle_instances = data.get("idle", {}).get("idle_instances", [])
    if not idle_instances:
        return '<div class="section-empty">No idle instances detected.</div>'

    pricing = {}
    for inst in data.get("savings", {}).get("cost_details", []):
        pricing[inst["instance_id"]] = float(inst.get("monthly_cost", 0))

    sorted_idle = sorted(idle_instances, key=lambda x: pricing.get(x["instance_id"], 0), reverse=True)

    rows = []
    for inst in sorted_idle:
        mc = pricing.get(inst["instance_id"], 0)
        cost_cls = " cost-high" if mc >= 200 else ""
        launch = inst.get("launch_time", "")[:10]
        rows.append(
            f"<tr>"
            f'<td class="mono">{inst["instance_id"]}</td>'
            f'<td>{inst.get("name", "UNTAGGED")}</td>'
            f'<td class="mono">{inst.get("instance_type", "")}</td>'
            f'<td class="text-right cost-high">{float(inst.get("avg_cpu", 0)):.2f}%</td>'
            f"<td>{launch}</td>"
            f'<td class="text-right cost{cost_cls}">{fmt_cost(mc)}</td>'
            f"</tr>"
        )

    idle_count = len(idle_instances)
    total_count = len(data.get("savings", {}).get("cost_details", []))

    return (
        f"<table><thead><tr>"
        f'<th>Instance ID</th><th>Name</th><th>Type</th>'
        f'<th class="text-right">Avg CPU %</th><th>Launch Date</th>'
        f'<th class="text-right">Monthly Cost ($)</th>'
        f"</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def build_compliance_section(data):
    comp = data.get("compliance", {})
    rate = float(comp.get("compliance_rate", 0))
    non_compliant = comp.get("non_compliant_instances", [])
    required = comp.get("required_tags", [])

    bar_w = compliance_bar_width(rate)
    bar_c = compliance_bar_color(rate)

    parts = [
        f'<div style="padding: 16px 20px; font-size: 14px; color: #666;">'
        f'Required tags: {", ".join(f"<strong>{t}</strong>" for t in required)}'
        f"</div>",
        f'<div class="compliance-bar">'
        f'<div class="compliance-fill" style="width: {bar_w}%; background: {bar_c};">{fmt_pct(rate)}</div>'
        f"</div>",
    ]

    if non_compliant:
        # Aggregate missing tags
        missing_counts = {}
        for inst in non_compliant:
            for tag in inst.get("missing_tags", []):
                missing_counts[tag] = missing_counts.get(tag, 0) + 1

        if missing_counts:
            tag_rows = []
            total_instances = len(non_compliant) + int(rate / 100 * len(non_compliant) / (1 - rate / 100)) if rate < 100 else len(non_compliant)
            for tag, count in sorted(missing_counts.items(), key=lambda x: -x[1]):
                tag_rows.append(
                    f"<tr><td>{tag}</td>"
                    f'<td class="text-center cost-high">{count}</td>'
                    f"</tr>"
                )
            parts.append(
                "<table><thead><tr>"
                '<th>Missing Tag</th><th class="text-center">Instances Missing</th>'
                "</tr></thead><tbody>"
                + "\n".join(tag_rows)
                + "</tbody></table>"
            )

    return "\n".join(parts)


def build_rightsizing_table(data):
    details = data.get("savings", {}).get("cost_details", [])
    candidates = [i for i in details if i.get("downsize_to", "N/A") != "N/A" and float(i.get("downsize_savings", 0)) > 0]

    if not candidates:
        return '<div class="section-empty">No rightsizing candidates found.</div>'

    sorted_candidates = sorted(candidates, key=lambda x: float(x.get("downsize_savings", 0)), reverse=True)

    rows = []
    for inst in sorted_candidates:
        mc = float(inst.get("monthly_cost", 0))
        ds = float(inst.get("downsize_savings", 0))
        new_cost = mc - ds
        rows.append(
            f"<tr>"
            f'<td class="mono">{inst["instance_id"]}</td>'
            f'<td>{inst.get("name", "UNTAGGED")}</td>'
            f'<td class="mono">{inst["instance_type"]}</td>'
            f'<td class="mono cost-savings">{inst["downsize_to"]}</td>'
            f'<td class="text-right">{fmt_cost(mc)}</td>'
            f'<td class="text-right">{fmt_cost(new_cost)}</td>'
            f'<td class="text-right cost cost-savings">{fmt_cost(ds)}</td>'
            f"</tr>"
        )

    total_savings = sum(float(i.get("downsize_savings", 0)) for i in candidates)

    return (
        "<table><thead><tr>"
        '<th>Instance ID</th><th>Name</th><th>Current Type</th><th>Recommended</th>'
        '<th class="text-right">Current ($/mo)</th><th class="text-right">After ($/mo)</th>'
        '<th class="text-right">Savings ($/mo)</th>'
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody><tfoot>"
        f'<tr style="font-weight:700; background:#f0f5ff;">'
        f'<td colspan="6">Total potential savings ({len(candidates)} candidates)</td>'
        f'<td class="text-right cost-savings">{fmt_cost(total_savings)}</td>'
        f"</tr></tfoot></table>"
    )


def build_ebs_table(data):
    ebs = data.get("ebs_waste", {})
    unattached = ebs.get("unattached_volumes", [])
    stopped = ebs.get("stopped_volumes", [])

    if not unattached and not stopped:
        return '<div class="section-empty">No EBS volume waste detected.</div>'

    rows = []
    total_cost = 0
    total_gb = 0

    for vol in sorted(unattached, key=lambda x: float(x.get("monthly_cost", 0)), reverse=True):
        mc = float(vol.get("monthly_cost", 0))
        total_cost += mc
        total_gb += int(vol.get("size_gb", 0))
        rows.append(
            f"<tr>"
            f'<td class="mono">{vol["volume_id"]}</td>'
            f'<td class="text-right">{vol["size_gb"]}</td>'
            f'<td class="mono">{vol.get("volume_type", "")}</td>'
            f'<td><span class="tag-missing">unattached</span></td>'
            f"<td>N/A</td>"
            f'<td class="text-right cost">{fmt_cost(mc)}</td>'
            f"</tr>"
        )

    for vol in sorted(stopped, key=lambda x: float(x.get("monthly_cost", 0)), reverse=True):
        mc = float(vol.get("monthly_cost", 0))
        total_cost += mc
        total_gb += int(vol.get("size_gb", 0))
        rows.append(
            f"<tr>"
            f'<td class="mono">{vol["volume_id"]}</td>'
            f'<td class="text-right">{vol["size_gb"]}</td>'
            f'<td class="mono">{vol.get("volume_type", "")}</td>'
            f'<td><span class="tag-missing">stopped-attached</span></td>'
            f'<td class="mono">{vol.get("attached_instance", "")}</td>'
            f'<td class="text-right cost">{fmt_cost(mc)}</td>'
            f"</tr>"
        )

    vol_count = len(unattached) + len(stopped)

    return (
        "<table><thead><tr>"
        '<th>Volume ID</th><th class="text-right">Size (GB)</th><th>Type</th>'
        '<th>State</th><th>Attached Instance</th><th class="text-right">Monthly Cost ($)</th>'
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody><tfoot>"
        f'<tr style="font-weight:700; background:#f0f5ff;">'
        f"<td colspan=\"5\">Total EBS waste ({vol_count} volumes, {total_gb:,} GB)</td>"
        f'<td class="text-right cost cost-high">{fmt_cost(total_cost)}</td>'
        f"</tr></tfoot></table>"
    )


def build_eip_table(data):
    eip = data.get("eip_waste", {})
    unattached = eip.get("unattached", [])

    if not unattached:
        return '<div class="section-empty">No unattached Elastic IPs detected.</div>'

    rows = []
    for addr in unattached:
        rows.append(
            f"<tr>"
            f'<td class="mono">{addr["allocation_id"]}</td>'
            f'<td class="mono">{addr["public_ip"]}</td>'
            f'<td><span class="tag-missing">unattached</span></td>'
            f'<td class="text-right cost">{fmt_cost(addr.get("monthly_waste", 3.65))}</td>'
            f"</tr>"
        )

    total = float(eip.get("monthly_waste", 0))

    return (
        "<table><thead><tr>"
        '<th>Allocation ID</th><th>Public IP</th><th>State</th>'
        '<th class="text-right">Monthly Waste ($)</th>'
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody><tfoot>"
        f'<tr style="font-weight:700; background:#f0f5ff;">'
        f"<td colspan=\"3\">Total EIP waste ({len(unattached)} unattached Elastic IPs)</td>"
        f'<td class="text-right cost cost-high">{fmt_cost(total)}</td>'
        f"</tr></tfoot></table>"
    )


TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AWS EC2 Cost Investigation Report</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: "Red Hat Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #333; background: #f5f5f5; line-height: 1.6; }}
  .header {{ background: #1A1A1A; color: #fff; padding: 24px 40px; }}
  .header h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 4px; }}
  .header .subtitle {{ color: #ccc; font-size: 14px; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 40px; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-top: 4px solid #0066CC; }}
  .card.alert {{ border-top-color: #EE0000; }}
  .card.success {{ border-top-color: #3E8635; }}
  .card .card-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: #666; margin-bottom: 4px; }}
  .card .card-value {{ font-size: 28px; font-weight: 700; }}
  .card .card-detail {{ font-size: 13px; color: #888; margin-top: 4px; }}
  section {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 24px; overflow: hidden; }}
  section h2 {{ font-size: 18px; font-weight: 600; padding: 16px 20px; border-bottom: 1px solid #e0e0e0; }}
  .section-empty {{ padding: 24px 20px; color: #888; text-align: center; font-style: italic; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ text-align: left; padding: 10px 16px; background: #f9f9f9; font-weight: 600; color: #555; border-bottom: 2px solid #e0e0e0; white-space: nowrap; }}
  td {{ padding: 10px 16px; border-bottom: 1px solid #f0f0f0; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
  tr:hover td {{ background: #f0f5ff; }}
  .mono {{ font-family: "Red Hat Mono", "SF Mono", "Consolas", monospace; font-size: 13px; }}
  .text-right {{ text-align: right; }}
  .text-center {{ text-align: center; }}
  .cost {{ font-weight: 600; }}
  .cost-high {{ color: #EE0000; }}
  .cost-savings {{ color: #3E8635; }}
  .tag-missing {{ display: inline-block; background: #fde8e8; color: #c00; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin: 2px; }}
  .compliance-bar {{ height: 24px; background: #e0e0e0; border-radius: 12px; overflow: hidden; margin: 16px 20px; }}
  .compliance-fill {{ height: 100%; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; font-weight: 600; }}
  .footer {{ text-align: center; padding: 24px 40px; color: #999; font-size: 12px; border-top: 1px solid #e0e0e0; margin-top: 16px; }}
  @media print {{
    body {{ background: #fff; }}
    .header {{ background: #333; }}
    section {{ box-shadow: none; border: 1px solid #ddd; }}
    .card {{ box-shadow: none; border: 1px solid #ddd; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>AWS EC2 Cost Investigation Report</h1>
  <div class="subtitle">{date} | Region: {region} | Generated by Claude via AAP</div>
</div>

<div class="container">

  <div class="summary-grid">
    {summary_cards}
  </div>

  <section>
    <h2>Cost Breakdown</h2>
    {cost_table}
  </section>

  <section>
    <h2>Idle Instances ({idle_heading})</h2>
    {idle_table}
  </section>

  <section>
    <h2>Tag Compliance</h2>
    {compliance_section}
  </section>

  <section>
    <h2>Rightsizing Recommendations</h2>
    {rightsizing_table}
  </section>

  <section>
    <h2>EBS Volume Waste</h2>
    {ebs_table}
  </section>

  <section>
    <h2>Elastic IP Waste</h2>
    {eip_table}
  </section>

</div>

<div class="footer">
  Generated on {timestamp} | Prices are approximate on-demand rates for {region} (Linux) | Not a bill -- use AWS Cost Explorer for actual spend
</div>

</body>
</html>
"""


def generate_report(data):
    region = data.get("region", "us-east-1")
    date = data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    idle_count = int(data.get("idle", {}).get("idle_count", 0))
    running_count = len(data.get("savings", {}).get("cost_details", []))
    idle_heading = f"{idle_count} of {running_count} running" if running_count > 0 else "no running instances"

    return TEMPLATE.format(
        date=date,
        region=region,
        timestamp=timestamp,
        summary_cards=build_summary_cards(data),
        cost_table=build_cost_table(data),
        idle_heading=idle_heading,
        idle_table=build_idle_table(data),
        compliance_section=build_compliance_section(data),
        rightsizing_table=build_rightsizing_table(data),
        ebs_table=build_ebs_table(data),
        eip_table=build_eip_table(data),
    )


def main():
    parser = argparse.ArgumentParser(description="Generate HTML cost report from JSON data")
    parser.add_argument("input", help="Path to JSON data file")
    parser.add_argument("-o", "--output", help="Output HTML file (default: auto-named in reports/)")
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    html = generate_report(data)

    if args.output:
        out_path = Path(args.output)
    else:
        date = data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        out_path = Path(args.input).parent / f"ec2-cost-report-{date}-{ts}.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
