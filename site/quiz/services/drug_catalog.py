"""Bounded, source-labelled decision support. No dose calculation or safety clearance.

Sources checked 2026-10-27. Word assertions of blanket safety are intentionally
not converted to a green/compatible status. Rules are symmetric only for overlap;
source-specific washout intervals are directional and listed separately.
"""
import re
import unicodedata

VERSION = "medication-rules-2026-10-27-2"
SOURCES = {
    "word": {"title": "Препараты.docx", "url": "/doctor/drug-sources/preparaty/", "kind": "Предоставленный материал"},
    "vidal": {"title": "Видаль · Авиандр: взаимодействия и противопоказания", "url": "https://www.vidal.ru/drugs/aviandr", "kind": "Справочник"},
    "aviandr": {"title": "Авиандр · информация производителя", "url": "https://aviandr.ru/patient", "kind": "Производитель"},
    "clozapine": {"title": "Инструкция клозапина · раздел 7.1", "url": "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=d36c298b-7292-46e0-898f-43d3e11c3cf4", "kind": "Инструкция производителя"},
    "lamotrigine": {"title": "GSK · инструкция Lamictal", "url": "https://gskpro.com/content/dam/global/hcpportal/en_US/Prescribing_Information/Lamictal/pdf/LAMICTAL-PI-MG.PDF", "kind": "Инструкция производителя"},
    "linezolid": {"title": "Красфарма · инструкция линезолида", "url": "https://kraspharma.ru/upload/iblock/2c4/ry40igoh8xa9s5jcez7xorciys7lyoic.pdf", "kind": "Инструкция производителя"},
    "aviandr_smpc": {"title": "Сотекс · ОХЛП Авиандра, раздел 4.5", "url": "https://sotex.ru/upload/iblock/aa7/rs9bqj20982r3uaisztx5mefa9t3qdhw/OKHLP_maritupirdin-21.05.25.pdf", "kind": "Официальная информация о препарате"},
    "olanzapine_ema": {"title": "EMA · инструкция Zyprexa, раздел 4.5", "url": "https://www.ema.europa.eu/en/documents/product-information/zyprexa-epar-product-information_en.pdf", "kind": "Регуляторная инструкция"},
    "quetiapine_emc": {"title": "eMC · инструкция Seroquel, разделы 4.3–4.5", "url": "https://www.medicines.org.uk/emc/product/7550/smpc", "kind": "Инструкция производителя, Великобритания"},
    "ciprofloxacin_qt": {"title": "HPRA · инструкция ципрофлоксацина", "url": "https://assets.hpra.ie/products/Human/27944/LicenseSPC_PA1410-028-005_18052017125143.pdf", "kind": "Регуляторная инструкция"},
    "macrolide_statins": {"title": "NHS SPS · макролиды и статины", "url": "https://sps.nhs.uk/articles/managing-interactions-between-macrolides-and-statins/", "kind": "Руководство специалистов по лекарствам"},
    "simvastatin_label": {"title": "Health Canada · инструкция симвастатина", "url": "https://pdf.hres.ca/dpd_pm/00057635.PDF", "kind": "Регуляторная инструкция"},
    "ssri_mhra": {"title": "MHRA · взаимодействия СИОЗС", "url": "https://cpd.mhra.gov.uk/ssri/CON146583_21", "kind": "Информация регулятора"},
    "sertraline_emc": {"title": "eMC · инструкция сертралина, раздел 4.5", "url": "https://www.medicines.org.uk/emc/product/101299/smpc", "kind": "Инструкция производителя, Великобритания"},
    "linezolid_rosuvastatin": {"title": "Drugs.com · линезолид и розувастатин", "url": "https://www.drugs.com/drug-interactions/linezolid-with-rosuvastatin-1472-0-2031-0.html", "kind": "Специализированный справочник, потенциальный риск"},
    "linezolid_atorvastatin": {"title": "Drugs.com · линезолид и аторвастатин", "url": "https://www.drugs.com/interactions-check.php?drug_list=276-0%2C1472-0&professional=1", "kind": "Специализированный справочник, потенциальный риск"},
    "rls_aviandr": {"title": "РЛС · инструкция Авиандра", "url": "https://www.rlsnet.ru/drugs/aviandr-89501", "kind": "Описание на основе инструкции производителя"},
}


def drug(code, name, group, tags=(), aliases=()):
    return {"code": code, "name": name, "group": group, "tags": set(tags), "aliases": aliases}


CATALOG = [
    drug("aviandr", "Авиандр (маритупирдин)", "Анксиолитики", ["aviandr"], ["Авиандр", "маритупирдин", "aviandr", "maritupirdine"]),
    drug("clozapine", "Клозапин", "Антипсихотики", ["antipsychotic", "clozapine"], ["азалептин", "клозастен", "clozapine"]),
    drug("olanzapine", "Оланзапин", "Антипсихотики", ["antipsychotic", "beta_lactam_note"], ["olanzapine"]),
    drug("risperidone", "Рисперидон", "Антипсихотики", ["antipsychotic", "beta_lactam_note", "risperidone"], ["risperidone"]),
    drug("quetiapine", "Кветиапин", "Антипсихотики", ["antipsychotic", "beta_lactam_note"], ["quetiapine"]),
    drug("haloperidol", "Галоперидол", "Антипсихотики", ["antipsychotic", "haloperidol"], ["haloperidol"]),
    drug("ziprasidone", "Зипрасидон", "Антипсихотики", ["antipsychotic"]),
    drug("chlorpromazine", "Хлорпромазин", "Антипсихотики", ["antipsychotic", "phenothiazine"], ["аминазин"]),
    drug("trifluoperazine", "Трифлуоперазин", "Антипсихотики", ["antipsychotic", "phenothiazine"], ["трифтазин"]),
    drug("sertraline", "Сертралин", "Антидепрессанты", ["antidepressant", "ssri", "serotonergic", "ssri_coc_note"]),
    drug("citalopram", "Циталопрам", "Антидепрессанты", ["antidepressant", "ssri", "serotonergic", "ssri_coc_note"]),
    drug("escitalopram", "Эсциталопрам", "Антидепрессанты", ["antidepressant", "ssri", "serotonergic", "ssri_coc_note"]),
    drug("fluvoxamine", "Флувоксамин", "Антидепрессанты", ["antidepressant", "ssri", "serotonergic", "statin_inhibitor", "strong_1a2"]),
    drug("nefazodone", "Нефазодон", "Антидепрессанты", ["antidepressant", "statin_inhibitor"]),
    drug("amitriptyline", "Амитриптилин", "Антидепрессанты", ["antidepressant", "tca", "serotonergic"]),
    drug("clomipramine", "Кломипрамин", "Антидепрессанты", ["antidepressant", "tca", "serotonergic"]),
    drug("lamotrigine", "Ламотриджин", "Нормотимики", ["lamotrigine"], ["ламиктал"]),
    drug("ciprofloxacin", "Ципрофлоксацин", "Антибиотики", ["strong_1a2"], ["ципролет", "цифран", "ciprofloxacin"]),
    drug("erythromycin", "Эритромицин", "Антибиотики", ["qt_antibiotic"]),
    drug("clarithromycin", "Кларитромицин", "Антибиотики", ["qt_antibiotic"], ["клацид"]),
    drug("azithromycin", "Азитромицин", "Антибиотики", ["qt_antibiotic"], ["сумамед"]),
    drug("levofloxacin", "Левофлоксацин", "Антибиотики", ["qt_antibiotic"]),
    drug("moxifloxacin", "Моксифлоксацин", "Антибиотики", ["qt_antibiotic"]),
    drug("chloramphenicol", "Хлорамфеникол", "Антибиотики", ["myelosuppressive_antibiotic"], ["левомицетин"]),
    drug("amoxicillin", "Амоксициллин", "Антибиотики", ["beta_lactam"]),
    drug("amoxicillin_clavulanate", "Амоксициллин + клавулановая кислота", "Антибиотики", ["beta_lactam"], ["амоксиклав", "аугментин"]),
    drug("ceftriaxone", "Цефтриаксон", "Антибиотики", ["beta_lactam"]),
    drug("linezolid", "Линезолид", "Антибиотики", ["mao", "linezolid"], ["линезолид", "linezolid", "зивокс"]),
    drug("moclobemide", "Моклобемид", "Ингибиторы МАО", ["mao"]),
    drug("selegiline", "Селегилин", "Ингибиторы МАО", ["mao"]),
    drug("simvastatin", "Симвастатин", "Статины", ["statin_3a4"]),
    drug("atorvastatin", "Аторвастатин", "Статины", ["statin_3a4"]),
    drug("rosuvastatin", "Розувастатин", "Статины", ["statin_alternative"]),
    drug("pravastatin", "Правастатин", "Статины", ["statin_alternative"]),
    drug("coc_estrogen", "КОК с эстрогеном", "Гормональные препараты", ["coc", "estrogen"], ["кок", "комбинированные оральные контрацептивы", "этинилэстрадиол + левоноргестрел"]),
    drug("estrogen_hrt", "Эстрогены (ЗГТ)", "Гормональные препараты", ["estrogen"], ["згт эстрогены", "эстрогены", "эстрадиол"]),
    drug("diazepam", "Диазепам", "Бензодиазепины", ["sedative"]),
    drug("phenobarbital", "Фенобарбитал", "Барбитураты", ["sedative"]),
    drug("diphenhydramine", "Дифенгидрамин", "Антигистаминные", ["sedative", "cyp2d6"], ["димедрол"]),
    drug("terbinafine", "Тербинафин", "Противогрибковые", ["cyp2d6"]),
    drug("quinidine", "Хинидин", "Другие препараты", ["cyp2d6"]),
    drug("hydroxychloroquine", "Гидроксихлорохин", "Другие препараты", ["cyp2d6"]),
    drug("tramadol", "Трамадол", "Анальгетики", ["serotonergic"]),
    drug("fentanyl", "Фентанил", "Анальгетики", ["serotonergic"]),
    drug("lithium", "Литий", "Нормотимики", ["serotonergic"], ["карбонат лития"]),
    drug("sumatriptan", "Суматриптан", "Триптаны", ["serotonergic"]),
    drug("tryptophan", "L-триптофан", "Другие препараты", ["serotonergic"], ["триптофан"]),
    drug("st_johns_wort", "Зверобой продырявленный", "Растительные средства", ["serotonergic"], ["зверобой"]),
    drug("ethanol", "Препарат, содержащий этанол", "Другие препараты", ["ethanol"], ["этанол"]),
]
for item in CATALOG:
    item['tags'].add(item['code'])
    if item['code'] in {'erythromycin', 'clarithromycin', 'nefazodone'}:
        item['tags'].add('quetiapine_inhibitor')
    if item['code'] in {'erythromycin', 'clarithromycin'}:
        item['tags'].add('strong_macrolide')
    if item['code'] in {'simvastatin', 'atorvastatin', 'rosuvastatin', 'pravastatin'}:
        item['tags'].add('statin')
BY_CODE = {d["code"]: d for d in CATALOG}


def normalize(value):
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").replace("®", "")
    return re.sub(r"\s+", " ", text).strip()


ALIASES = {normalize(alias): d for d in CATALOG for alias in (d["name"], *d["aliases"])}


def resolve(name):
    normalized = normalize(name)
    if normalized in ALIASES:
        return ALIASES[normalized]
    # Only strip an unambiguous trailing strength, never arbitrary/fuzzy text.
    short = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:мг|мкг|г|mg|mcg)(?:\s*таблетки)?$", "", normalized)
    return ALIASES.get(short)


def rule(code, left, right, message, detail, sources=("word",), level="warning", alternatives=()):
    return {"id": code, "left": left, "right": right, "message": message, "detail": detail,
            "source_ids": sources, "level": level, "alternatives": alternatives}


RULES = [
    rule('olanzapine-cyp1a2', 'olanzapine', 'strong_1a2', 'Может повыситься концентрация оланзапина', 'В инструкции описано влияние ципрофлоксацина и флувоксамина через CYP1A2. Нужны оценка схемы и клиническое наблюдение; дозу выбирает врач.', ('olanzapine_ema',)),
    rule('ciprofloxacin-antipsychotic-qt', 'ciprofloxacin', 'antipsychotic', 'Осторожность: суммирование влияния на QT', 'Инструкция ципрофлоксацина предупреждает о сочетании с антипсихотиками из-за возможного удлинения QT. Учитывайте конкретный препарат и факторы риска пациента.', ('ciprofloxacin_qt',)),
    rule('ciprofloxacin-tca-qt', 'ciprofloxacin', 'tca', 'Осторожность: суммирование влияния на QT', 'В инструкции ципрофлоксацина отдельно указана осторожность с трициклическими антидепрессантами.', ('ciprofloxacin_qt',)),
    rule('ciprofloxacin-macrolide-qt', 'ciprofloxacin', 'qt_antibiotic', 'Проверьте совместное влияние на QT', 'Для сочетания ципрофлоксацина с макролидами или другими средствами, влияющими на QT, требуется оценка риска. Это не автоматический запрет всей группы.', ('ciprofloxacin_qt',)),
    rule('quetiapine-inhibitors', 'quetiapine', 'quetiapine_inhibitor', 'В инструкции сочетание противопоказано', 'В инструкции Seroquel указано противопоказание к одновременному применению с эритромицином, кларитромицином и нефазодоном — ингибиторами CYP3A4.', ('quetiapine_emc',), level='blocked'),
    rule('quetiapine-serotonergic', 'quetiapine', 'serotonergic', 'Требуется осторожность с серотонинергическими средствами', 'Инструкция кветиапина указывает риск серотонинового синдрома при сочетании с серотонинергическими препаратами. Клиническую значимость оценивает врач.', ('quetiapine_emc',)),
    rule('simvastatin-macrolides', 'simvastatin', 'strong_macrolide', 'Противопоказанное сочетание со статином', 'Кларитромицин и эритромицин противопоказаны с симвастатином. Следует пересмотреть сочетание; система сама не отменяет препарат.', ('macrolide_statins',), level='blocked', alternatives=('rosuvastatin', 'pravastatin')),
    rule('simvastatin-nefazodone', 'simvastatin', 'nefazodone', 'В инструкции сочетание противопоказано', 'Нефазодон входит в перечень ингибиторов CYP3A4, с которыми симвастатин противопоказан. Уточните схему по инструкции конкретного препарата.', ('simvastatin_label',), level='blocked'),
    rule('atorvastatin-macrolides', 'atorvastatin', 'strong_macrolide', 'Может повыситься концентрация аторвастатина', 'Руководство рекомендует по возможности избегать сочетания с кларитромицином или эритромицином. Врач оценивает другой антибиотик или пересмотр терапии статином.', ('macrolide_statins',), alternatives=('rosuvastatin', 'pravastatin')),
    rule('pravastatin-macrolides', 'pravastatin', 'strong_macrolide', 'Нужна осторожность и наблюдение', 'При сочетании правастатина с кларитромицином или эритромицином руководство рекомендует учитывать возможность взаимодействия и наблюдать за побочными эффектами.', ('macrolide_statins',)),
    rule('rosuvastatin-macrolides', 'rosuvastatin', 'strong_macrolide', 'Есть отдельные сведения об этой паре', 'В руководстве допускается продолжение розувастатина с кларитромицином или эритромицином. Это не подтверждает совместимость со всеми другими лекарствами пациента.', ('macrolide_statins',), level='note'),
    rule('azithromycin-statins', 'azithromycin', 'statin', 'Есть сведения; учитывайте редкие нежелательные реакции', 'Руководство допускает сочетание с азитромицином, но отмечает редкие сообщения о нежелательных реакциях. Общая совместимость всей схемы не установлена.', ('macrolide_statins',), level='note'),
    rule('aviandr-ciprofloxacin-pk', 'aviandr', 'ciprofloxacin', 'В исследовании значимого изменения фармакокинетики не выявлено', 'ОХЛП сообщает об исследовании Авиандра с ципрофлоксацином без значимого изменения основных фармакокинетических параметров. Этот ограниченный результат не является общей гарантией безопасности сочетания.', ('aviandr_smpc',), level='note'),
    rule('aviandr-coc-pk', 'aviandr', 'coc', 'Предполагается отсутствие значимого фармакокинетического влияния', 'ОХЛП предполагает отсутствие значимого влияния других ингибиторов CYP1A2, включая КОК, на фармакокинетику Авиандра. Это предположение, а не отдельное клиническое исследование всех составов КОК.', ('aviandr_smpc',), level='note'),
    rule('ssri-serotonergic', 'ssri', 'serotonergic', 'Возможно усиление серотонинергического действия', 'Регулятор предупреждает о сочетании СИОЗС с другими серотонинергическими средствами. Возможен серотониновый синдром; требуется оценка сочетания и наблюдение.', ('ssri_mhra',)),
    rule('ssri-mao-review', 'ssri', 'mao', 'Следует избегать сочетания без специализированной оценки', 'MHRA отмечает риск усиления серотонинергического действия. Для конкретного СИОЗС противопоказания и интервалы отмены нужно сверить с его инструкцией.', ('ssri_mhra',)),
    rule('sertraline-maoi', 'sertraline', 'mao', 'В инструкции сертралина сочетание не допускается', 'Инструкция указывает недопустимость сочетания с селегилином, моклобемидом и линезолидом. При смене терапии также важен интервал после отмены, который зависит от препарата.', ('sertraline_emc',), level='blocked'),
    rule('linezolid-rosuvastatin-neuropathy', 'linezolid', 'rosuvastatin', 'Справочник отмечает потенциальный риск нейропатии', 'Drugs.com относит эту пару к сочетаниям, требующим наблюдения из-за возможного суммирования нейротоксических эффектов. Это справочная оценка потенциального риска, не доказательство неизбежного осложнения.', ('linezolid_rosuvastatin',)),
    rule('linezolid-atorvastatin-neuropathy', 'linezolid', 'atorvastatin', 'Справочник отмечает потенциальный риск нейропатии', 'В профессиональном справочнике указана необходимость осторожности и наблюдения за признаками нейропатии. Клиническую значимость для конкретного пациента оценивает врач.', ('linezolid_atorvastatin',)),
    rule("linezolid-serotonergic", "linezolid", "serotonergic", "Риск серотонинового синдрома", "В инструкции линезолида отмечен риск при сочетании с серотонинергическими препаратами; совместное применение требует отдельной оценки и тщательного наблюдения.", ("linezolid",)),
    rule("aviandr-mao", "aviandr", "mao", "Противопоказанное сочетание", "Авиандр нельзя сочетать с ингибиторами МАО; после их отмены необходим интервал 14 дней.", ("vidal", "aviandr"), level="blocked"),
    rule("aviandr-serotonergic", "aviandr", "serotonergic", "Риск серотонинергических эффектов", "Возможен серотониновый синдром. Требуется пересмотр сочетания по инструкции и оценка врачом.", ("vidal", "aviandr")),
    rule("aviandr-ssri-cyp", "aviandr", "ssri", "Возможно изменение концентрации Авиандра", "Видаль указывает осторожность при совместном применении с СИОЗС, в том числе из-за ингибирования CYP2D6.", ("vidal",)),
    rule("aviandr-sedative", "aviandr", "sedative", "Возможна усиленная седация", "Оцените угнетение ЦНС и необходимость наблюдения при совместном применении.", ("vidal", "aviandr")),
    rule("aviandr-antipsychotic", "aviandr", "antipsychotic", "Возможна усиленная седация", "Авиандр может усиливать седативный эффект нейролептиков; требуется осторожность.", ("vidal", "aviandr")),
    rule("aviandr-cyp2d6", "aviandr", "cyp2d6", "Возможно повышение концентрации Авиандра", "Ингибирование CYP2D6 может изменить концентрацию маритупирдина; проверьте инструкцию конкретного препарата.", ("vidal",)),
    rule("aviandr-ethanol", "aviandr", "ethanol", "Следует избегать сочетания с этанолом", "Может усиливаться угнетение ЦНС. Уточните состав принимаемого средства.", ("vidal", "aviandr")),
    rule("clozapine-cyp1a2", "clozapine", "strong_1a2", "Может повыситься концентрация клозапина", "Ципрофлоксацин и флувоксамин ингибируют CYP1A2. Рассмотрите другое сочетание; при совместном применении нужен контроль концентрации и побочных эффектов. Дозу система не рассчитывает.", ("word", "clozapine")),
    rule("antipsychotic-qt-antibiotic", "antipsychotic", "qt_antibiotic", "Риск удлинения QT", "В материале отмечен риск аритмий, особенно для галоперидола и зипрасидона. Сверьте инструкцию, оцените ЭКГ и другое сочетание."),
    rule("antipsychotic-myelosuppression", "antipsychotic", "myelosuppressive_antibiotic", "Риск угнетения кроветворения", "В материале рекомендовано избегать сочетания, особенно с клозапином; при необходимости требуется контроль картины крови."),
    rule("phenothiazine-coc", "phenothiazine", "coc", "Возможно усиление побочных эффектов", "КОК могут повышать концентрацию фенотиазинов. В материале рекомендован контроль седации и экстрапирамидных нарушений."),
    rule("clozapine-coc", "clozapine", "coc", "Может повыситься концентрация клозапина", "При совместном применении с эстрогенсодержащими КОК нужен контроль побочных эффектов и концентрации; при начале или отмене КОК пересмотрите схему.", ("word", "clozapine")),
    rule("lamotrigine-estrogen", "lamotrigine", "estrogen", "Возможно снижение эффективности ламотриджина", "Эстрогенсодержащие КОК снижают экспозицию ламотриджина. При начале и отмене нужен пересмотр схемы; для ЗГТ данные менее определённы, требуется наблюдение.", ("word", "lamotrigine")),
    rule("tca-estrogen", "tca", "estrogen", "Могут усилиться побочные эффекты ТЦА", "В материале указано возможное повышение концентрации трициклических антидепрессантов; требуется клиническое наблюдение."),
    rule("statin-inhibitor", "statin_3a4", "statin_inhibitor", "Риск повышения концентрации статина", "В материале отмечен риск миопатии для симвастатина и аторвастатина. Для обсуждения предложены розувастатин и правастатин; замену и дозу выбирает врач.", alternatives=("rosuvastatin", "pravastatin")),
    rule("beta-lactam-note", "beta_lactam_note", "beta_lactam", "В материале значимое взаимодействие не отмечено", "Это сведения только об этой паре, без подтверждения общей совместимости и без проверки остальных противопоказаний.", level="note"),
    rule("ssri-coc-note", "ssri_coc_note", "coc", "В материале значимое взаимодействие не отмечено", "Для этой пары в предоставленном материале значимое взаимодействие не описано. Другие ограничения требуют отдельной проверки.", level="note"),
    rule("statin-alternative-note", "statin_alternative", "antidepressant", "В материале значимое взаимодействие не отмечено", "Розувастатин и правастатин предложены в материале как варианты для обсуждения. Это не автоматическая рекомендация замены.", level="note"),
    rule("risperidone-coc-note", "risperidone", "coc", "Возможно небольшое изменение концентрации", "В материале обычно не требуется коррекция дозы; оцените необходимость наблюдения.", level="note"),
    rule("haloperidol-coc-unknown", "haloperidol", "coc", "В материале недостаточно данных об этой паре", "Для галоперидола с КОК в материале нет достоверных данных. Совместимость не установлена.", level="note"),
]

# No inferred intervals for other combinations. These are explicit source values;
# the moclobemide -> sertraline interval is deliberately a warning, not a fixed ban.
WASHOUT_RULES = [
    {'candidate': 'aviandr', 'previous': 'mao', 'days': 14, 'rule': 'aviandr-mao', 'level': 'blocked'},
    {'candidate': 'sertraline', 'previous': 'selegiline', 'days': 14, 'rule': 'sertraline-maoi', 'level': 'blocked'},
    {'candidate': 'selegiline', 'previous': 'sertraline', 'days': 7, 'rule': 'sertraline-maoi', 'level': 'blocked'},
    {'candidate': 'moclobemide', 'previous': 'sertraline', 'days': 7, 'rule': 'sertraline-maoi', 'level': 'blocked'},
    {'candidate': 'sertraline', 'previous': 'moclobemide', 'days': 14, 'rule': 'sertraline-maoi', 'level': 'warning',
     'detail': 'После моклобемида интервал перед сертралином может быть короче 14 дней; точный срок необходимо уточнить по инструкции и клинической ситуации. Система не устанавливает для этой пары универсальную дату разрешения.'},
]

# This is a dated, limited literature search, not a claim that no interaction exists.
SEARCHED_GAPS = {
    frozenset(('aviandr', code)): {'checked_at': '27.10.2026', 'sources': ('vidal', 'rls_aviandr', 'aviandr_smpc'),
        'comment': 'В проверенных описаниях Авиандра не удалось подтвердить отдельное правило для этой пары. Недостаточно информации для вывода о совместимости.'}
    for code in ('simvastatin', 'atorvastatin', 'rosuvastatin', 'pravastatin', 'lamotrigine', 'amoxicillin', 'amoxicillin_clavulanate', 'ceftriaxone', 'estrogen_hrt')
}


def pair_rules(first, second):
    return [r for r in RULES if (r["left"] in first["tags"] and r["right"] in second["tags"])
            or (r["right"] in first["tags"] and r["left"] in second["tags"])]
