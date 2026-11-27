"""Aggregate reports, without participant identifiers or secrets."""
import io
from pathlib import Path

def safe_cell(value):
    return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else value

def make_report(title,questions,rounds,fmt):
    out=io.BytesIO()
    if fmt=='xlsx':
        from openpyxl import Workbook
        from openpyxl.chart import BarChart, Reference
        from openpyxl.styles import Font, PatternFill
        wb=Workbook(); ws=wb.active; ws.title='Результаты'
        ws.append(['Мероприятие','Этап','Вопрос','Вариант','Голоса','Доля','Ответили'])
        for name,rs in rounds:
            for q,res in zip(questions,rs):
                for opt,n in zip(q['options'],res['counts']):
                    ws.append([safe_cell(title),safe_cell(name),safe_cell(q['question']),safe_cell(opt['label']),n,n/res['total'] if res['total'] else 0,res['total']])
                    ws.cell(ws.max_row,6).number_format='0.0%'
        for cell in ws[1]:
            cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='176C69')
        ws.freeze_panes='E2';ws.auto_filter.ref=ws.dimensions
        for col,width in zip('ABCDEFG',[30,24,60,32,12,12,12]): ws.column_dimensions[col].width=width
        for i,q in enumerate(questions):
            sheet=wb.create_sheet(f'Вопрос {i+1}')
            sheet.append([safe_cell(q['question']+(' — множественный выбор, доля ответивших' if q.get('multi') else ''))])
            sheet.append(['Вариант']+[safe_cell(name) for name,_ in rounds])
            for j,o in enumerate(q['options']):
                sheet.append([safe_cell(o['label'])]+[rs[i]['counts'][j]/rs[i]['total'] if rs[i]['total'] else 0 for _,rs in rounds])
                for cell in sheet[sheet.max_row][1:]:cell.number_format='0.0%'
            sheet.column_dimensions['A'].width=42
            for col in list(sheet.columns)[1:]:sheet.column_dimensions[col[0].column_letter].width=20
            chart=BarChart();chart.title='Доли ответов';chart.y_axis.numFmt='0%';chart.y_axis.scaling.max=1
            chart.add_data(Reference(sheet,min_col=2,max_col=len(rounds)+1,min_row=2,max_row=sheet.max_row),titles_from_data=True)
            chart.set_categories(Reference(sheet,min_col=1,min_row=3,max_row=sheet.max_row));sheet.add_chart(chart,'A11')
        wb.save(out);return out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font=next((Path(p) for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf','/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf','/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf') if Path(p).exists()),None)
    if font is None: raise FileNotFoundError('Install fonts-dejavu-core for Cyrillic reports')
    pdfmetrics.registerFont(TTFont('VotemeraReport',str(font)))
    styles={name:ParagraphStyle(name,fontName='VotemeraReport',fontSize=size,leading=size*1.35,textColor=colors.HexColor('#162132')) for name,size in [('title',19),('head',13),('body',9)]}
    def p(text,style='body'):return Paragraph(escape(str(text)),styles[style])
    story=[p(title,'title'),p('Анонимные агрегированные результаты'),Spacer(1,12)]
    for i,q in enumerate(questions):
        story.extend([p(f'{i+1}. {q["question"]}','head'),Spacer(1,6)])
        if q.get('multi'):story.append(p('Множественный выбор: доля ответивших, сумма может превышать 100%.'))
        for name,rs in rounds:
            res=rs[i];story.append(p(f'{name} — ответили: {res["total"]}'))
            table=[[p('Вариант'),p('Голоса'),p('Доля'),p('')]]
            for o,n in zip(q['options'],res['counts']):
                share=n/res['total'] if res['total'] else 0
                from reportlab.graphics.shapes import Drawing,Rect
                bar=Drawing(100,10);bar.add(Rect(0,1,100,8,fillColor=colors.HexColor('#e2e8ef'),strokeColor=None));bar.add(Rect(0,1,100*share,8,fillColor=colors.HexColor(o['color']),strokeColor=None))
                table.append([p(o['label']),p(n),p(f'{share:.1%}'),bar])
            t=Table(table,colWidths=[240,50,60,105],repeatRows=1)
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8ef')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story.extend([t,Spacer(1,8)])
        story.append(Spacer(1,10))
    SimpleDocTemplate(out,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=36,title=title).build(story)
    return out.getvalue(),'application/pdf'
