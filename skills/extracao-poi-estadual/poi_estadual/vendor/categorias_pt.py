# -*- coding: utf-8 -*-
"""
categorias_pt.py — Tradução e segmentação de categorias de POI para PORTUGUÊS claro.

Fonte das chaves: categorias do Overture Places (snake_case, ex. 'beauty_salon')
e rótulos hierárquicos da Foursquare OS Places (ex. 'Dining and Drinking > Bakery').
Para o que não estiver no mapa curado, `traduzir()` cai num tradutor por TOKENS
(palavra a palavra) — garante PT razoável em 100% dos casos, sem sobrar inglês.

API:
    traduzir(cat_overture) -> (categoria_pt, segmento)
    traduzir_fsq(label_hierarquico) -> (categoria_pt, segmento)
"""
import re

SEGMENTOS = [
    "Alimentação", "Comércio Varejista", "Serviços", "Saúde", "Educação",
    "Beleza e Estética", "Automotivo", "Financeiro", "Hospedagem",
    "Lazer e Cultura", "Religioso", "Imobiliário", "Indústria",
    "Público e Institucional", "Outros",
]

# ---- mapa CURADO (categoria Overture -> (PT, segmento)) ---------------------
CATEGORIAS = {
    # --- Alimentação ---
    "restaurant": ("Restaurante", "Alimentação"),
    "pizza_restaurant": ("Pizzaria", "Alimentação"),
    "fast_food_restaurant": ("Fast-food", "Alimentação"),
    "brazilian_restaurant": ("Restaurante Brasileiro", "Alimentação"),
    "japanese_restaurant": ("Restaurante Japonês", "Alimentação"),
    "italian_restaurant": ("Restaurante Italiano", "Alimentação"),
    "chinese_restaurant": ("Restaurante Chinês", "Alimentação"),
    "steakhouse": ("Churrascaria", "Alimentação"),
    "burger_restaurant": ("Hamburgueria", "Alimentação"),
    "seafood_restaurant": ("Restaurante de Frutos do Mar", "Alimentação"),
    "bakery": ("Padaria", "Alimentação"),
    "cafe": ("Cafeteria", "Alimentação"),
    "coffee_shop": ("Cafeteria", "Alimentação"),
    "bar": ("Bar", "Alimentação"),
    "pub": ("Bar", "Alimentação"),
    "ice_cream_shop": ("Sorveteria", "Alimentação"),
    "snack_bar": ("Lanchonete", "Alimentação"),
    "juice_bar": ("Casa de Sucos", "Alimentação"),
    "candy_store": ("Doceria", "Alimentação"),
    "food_truck": ("Food Truck", "Alimentação"),
    "bakery_and_dessert": ("Padaria e Confeitaria", "Alimentação"),
    "butcher_shop": ("Açougue", "Comércio Varejista"),

    # --- Comércio Varejista ---
    "supermarket": ("Supermercado", "Comércio Varejista"),
    "grocery_store": ("Mercearia", "Comércio Varejista"),
    "convenience_store": ("Loja de Conveniência", "Comércio Varejista"),
    "clothing_store": ("Loja de Roupas", "Comércio Varejista"),
    "shoe_store": ("Loja de Calçados", "Comércio Varejista"),
    "furniture_store": ("Loja de Móveis", "Comércio Varejista"),
    "hardware_store": ("Loja de Ferragens", "Comércio Varejista"),
    "electronics_store": ("Loja de Eletrônicos", "Comércio Varejista"),
    "pet_store": ("Pet Shop", "Comércio Varejista"),
    "bookstore": ("Livraria", "Comércio Varejista"),
    "jewelry_store": ("Joalheria", "Comércio Varejista"),
    "flowers_and_gifts_shop": ("Floricultura e Presentes", "Comércio Varejista"),
    "flower_shop": ("Floricultura", "Comércio Varejista"),
    "gift_shop": ("Loja de Presentes", "Comércio Varejista"),
    "toy_store": ("Loja de Brinquedos", "Comércio Varejista"),
    "sporting_goods_store": ("Loja de Artigos Esportivos", "Comércio Varejista"),
    "cosmetics_store": ("Loja de Cosméticos", "Comércio Varejista"),
    "department_store": ("Loja de Departamentos", "Comércio Varejista"),
    "market": ("Mercado", "Comércio Varejista"),
    "wholesale_store": ("Atacado", "Comércio Varejista"),
    "building_materials_store": ("Material de Construção", "Comércio Varejista"),
    "auto_parts_store": ("Autopeças", "Comércio Varejista"),
    "mobile_phone_store": ("Loja de Celulares", "Comércio Varejista"),
    "optician": ("Ótica", "Comércio Varejista"),
    "liquor_store": ("Loja de Bebidas", "Comércio Varejista"),
    "tobacco_store": ("Tabacaria", "Comércio Varejista"),
    "fabric_store": ("Loja de Tecidos", "Comércio Varejista"),
    "appliance_store": ("Loja de Eletrodomésticos", "Comércio Varejista"),

    # --- Serviços ---
    "professional_services": ("Serviços Profissionais", "Serviços"),
    "rental_service": ("Locação", "Serviços"),
    "home_security": ("Segurança Residencial", "Serviços"),
    "laundry_service": ("Lavanderia", "Serviços"),
    "dry_cleaner": ("Lavanderia a Seco", "Serviços"),
    "locksmith": ("Chaveiro", "Serviços"),
    "courier_and_delivery": ("Entregas e Correios", "Serviços"),
    "moving_company": ("Mudanças", "Serviços"),
    "advertising_agency": ("Agência de Publicidade", "Serviços"),
    "travel_agency": ("Agência de Viagens", "Serviços"),
    "employment_agency": ("Agência de Emprego", "Serviços"),
    "graphic_designer": ("Designer Gráfico", "Serviços"),
    "print_shop": ("Gráfica", "Serviços"),
    "veterinarian": ("Veterinário", "Saúde"),
    "lawyer": ("Advocacia", "Serviços"),
    "accountant": ("Contabilidade", "Serviços"),
    "notary": ("Cartório", "Público e Institucional"),
    "it_service": ("Serviços de TI", "Serviços"),
    "repair_service": ("Assistência Técnica", "Serviços"),
    "tailor": ("Alfaiataria/Costura", "Serviços"),
    "funeral_service": ("Funerária", "Serviços"),

    # --- Saúde ---
    "doctor": ("Médico", "Saúde"),
    "dentist": ("Dentista", "Saúde"),
    "hospital": ("Hospital", "Saúde"),
    "clinic": ("Clínica", "Saúde"),
    "medical_clinic": ("Clínica Médica", "Saúde"),
    "pharmacy": ("Farmácia", "Saúde"),
    "drugstore": ("Drogaria", "Saúde"),
    "physiotherapist": ("Fisioterapia", "Saúde"),
    "psychologist": ("Psicólogo", "Saúde"),
    "laboratory": ("Laboratório", "Saúde"),
    "medical_laboratory": ("Laboratório de Análises", "Saúde"),
    "health_food_store": ("Loja de Produtos Naturais", "Saúde"),
    "optometrist": ("Optometrista", "Saúde"),

    # --- Educação ---
    "school": ("Escola", "Educação"),
    "primary_school": ("Escola de Ensino Fundamental", "Educação"),
    "high_school": ("Escola de Ensino Médio", "Educação"),
    "preschool": ("Educação Infantil", "Educação"),
    "kindergarten": ("Creche", "Educação"),
    "university": ("Universidade", "Educação"),
    "college": ("Faculdade", "Educação"),
    "language_school": ("Escola de Idiomas", "Educação"),
    "driving_school": ("Autoescola", "Educação"),
    "tutoring_service": ("Reforço Escolar", "Educação"),
    "music_school": ("Escola de Música", "Educação"),
    "vocational_school": ("Escola Técnica", "Educação"),
    "library": ("Biblioteca", "Educação"),

    # --- Beleza e Estética ---
    "beauty_salon": ("Salão de Beleza", "Beleza e Estética"),
    "barber": ("Barbearia", "Beleza e Estética"),
    "hair_salon": ("Cabeleireiro", "Beleza e Estética"),
    "nail_salon": ("Manicure/Pedicure", "Beleza e Estética"),
    "spa": ("Spa", "Beleza e Estética"),
    "tattoo_and_piercing": ("Estúdio de Tatuagem", "Beleza e Estética"),
    "tattoo_parlor": ("Estúdio de Tatuagem", "Beleza e Estética"),
    "aesthetic_clinic": ("Clínica de Estética", "Beleza e Estética"),
    "barbershop": ("Barbearia", "Beleza e Estética"),
    "cosmetic_clinic": ("Clínica de Estética", "Beleza e Estética"),

    # --- Automotivo ---
    "automotive_repair": ("Oficina Mecânica", "Automotivo"),
    "car_repair": ("Oficina Mecânica", "Automotivo"),
    "car_dealer": ("Concessionária", "Automotivo"),
    "used_car_dealer": ("Revenda de Veículos", "Automotivo"),
    "car_wash": ("Lava-rápido", "Automotivo"),
    "gas_station": ("Posto de Combustível", "Automotivo"),
    "tire_shop": ("Borracharia", "Automotivo"),
    "motorcycle_repair": ("Oficina de Motos", "Automotivo"),
    "auto_body_shop": ("Funilaria e Pintura", "Automotivo"),
    "parking": ("Estacionamento", "Automotivo"),
    "car_rental": ("Locadora de Veículos", "Automotivo"),

    # --- Financeiro ---
    "bank": ("Banco", "Financeiro"),
    "atm": ("Caixa Eletrônico", "Financeiro"),
    "credit_union": ("Cooperativa de Crédito", "Financeiro"),
    "insurance_agency": ("Corretora de Seguros", "Financeiro"),
    "financial_service": ("Serviço Financeiro", "Financeiro"),
    "currency_exchange": ("Casa de Câmbio", "Financeiro"),
    "accounting_service": ("Contabilidade", "Financeiro"),

    # --- Hospedagem ---
    "hotel": ("Hotel", "Hospedagem"),
    "motel": ("Motel", "Hospedagem"),
    "hostel": ("Hostel", "Hospedagem"),
    "inn": ("Pousada", "Hospedagem"),
    "bed_and_breakfast": ("Pousada", "Hospedagem"),
    "guest_house": ("Casa de Hóspedes", "Hospedagem"),

    # --- Lazer e Cultura ---
    "gym": ("Academia", "Lazer e Cultura"),
    "fitness_center": ("Academia", "Lazer e Cultura"),
    "park": ("Parque", "Lazer e Cultura"),
    "playground": ("Playground", "Lazer e Cultura"),
    "movie_theater": ("Cinema", "Lazer e Cultura"),
    "theater": ("Teatro", "Lazer e Cultura"),
    "museum": ("Museu", "Lazer e Cultura"),
    "art_gallery": ("Galeria de Arte", "Lazer e Cultura"),
    "nightclub": ("Casa Noturna", "Lazer e Cultura"),
    "stadium": ("Estádio", "Lazer e Cultura"),
    "sports_club": ("Clube Esportivo", "Lazer e Cultura"),
    "swimming_pool": ("Piscina/Clube", "Lazer e Cultura"),
    "event_space": ("Espaço de Eventos", "Lazer e Cultura"),
    "dance_studio": ("Estúdio de Dança", "Lazer e Cultura"),
    "martial_arts_club": ("Academia de Artes Marciais", "Lazer e Cultura"),
    "landmark_and_historical_building": ("Marco/Edifício Histórico", "Lazer e Cultura"),
    "tourist_attraction": ("Ponto Turístico", "Lazer e Cultura"),

    # --- Religioso ---
    "church": ("Igreja", "Religioso"),
    "catholic_church": ("Igreja Católica", "Religioso"),
    "evangelical_church": ("Igreja Evangélica", "Religioso"),
    "temple": ("Templo", "Religioso"),
    "mosque": ("Mesquita", "Religioso"),
    "synagogue": ("Sinagoga", "Religioso"),
    "religious_organization": ("Organização Religiosa", "Religioso"),

    # --- Imobiliário ---
    "real_estate": ("Imobiliária", "Imobiliário"),
    "real_estate_agent": ("Corretor de Imóveis", "Imobiliário"),
    "condominium": ("Condomínio", "Imobiliário"),
    "apartment_building": ("Edifício Residencial", "Imobiliário"),
    "property_management": ("Administradora de Imóveis", "Imobiliário"),

    # --- Indústria ---
    "factory": ("Fábrica", "Indústria"),
    "warehouse": ("Galpão/Depósito", "Indústria"),
    "manufacturer": ("Indústria", "Indústria"),
    "industrial_company": ("Empresa Industrial", "Indústria"),
    "distribution_center": ("Centro de Distribuição", "Indústria"),

    # --- Público e Institucional ---
    "government_office": ("Órgão Público", "Público e Institucional"),
    "city_hall": ("Prefeitura", "Público e Institucional"),
    "police_station": ("Delegacia/Polícia", "Público e Institucional"),
    "fire_station": ("Corpo de Bombeiros", "Público e Institucional"),
    "post_office": ("Correios", "Público e Institucional"),
    "courthouse": ("Fórum", "Público e Institucional"),
    "public_utility": ("Concessionária de Serviço Público", "Público e Institucional"),
    "embassy": ("Embaixada/Consulado", "Público e Institucional"),
    "community_center": ("Centro Comunitário", "Público e Institucional"),

    # --- complementos (cauda comum vista em campo) ---
    "event_planning": ("Organização de Eventos", "Serviços"),
    "event_photography": ("Fotografia de Eventos", "Serviços"),
    "shopping": ("Compras/Comércio", "Comércio Varejista"),
    "automotive": ("Serviço Automotivo", "Automotivo"),
    "automotive_parts_and_accessories": ("Autopeças e Acessórios", "Automotivo"),
    "auto_detailing": ("Estética Automotiva", "Automotivo"),
    "fuel": ("Posto de Combustível", "Automotivo"),
    "stadium_arena": ("Estádio/Arena", "Lazer e Cultura"),
    "pitch": ("Campo/Quadra Esportiva", "Lazer e Cultura"),
    "sports_centre": ("Centro Esportivo", "Lazer e Cultura"),
    "sports_center": ("Centro Esportivo", "Lazer e Cultura"),
    "arts_and_entertainment": ("Artes e Entretenimento", "Lazer e Cultura"),
    "health_spa": ("Spa", "Beleza e Estética"),
    "beauty_and_spa": ("Beleza e Spa", "Beleza e Estética"),
    "desserts": ("Doceria/Sobremesas", "Alimentação"),
    "beer_wine_and_spirits": ("Loja de Bebidas", "Comércio Varejista"),
    "storage_facility": ("Self-storage/Depósito", "Serviços"),
    "information_technology_company": ("Empresa de TI", "Serviços"),
    "software_development": ("Desenvolvimento de Software", "Serviços"),
    "financial_advising": ("Assessoria Financeira", "Financeiro"),
    "physical_therapy": ("Fisioterapia", "Saúde"),
    "laundromat": ("Lavanderia", "Serviços"),
    "eyewear_and_optician": ("Ótica", "Comércio Varejista"),
    "travel_agents": ("Agência de Viagens", "Serviços"),
    "pilates_studio": ("Estúdio de Pilates", "Lazer e Cultura"),
    "education": ("Educação", "Educação"),
    "construction_company": ("Construtora", "Indústria"),
    "wedding_planning_service": ("Organização de Casamentos", "Serviços"),
    "musical_instrument_store": ("Loja de Instrumentos Musicais", "Comércio Varejista"),

    # --- v3.0.0: categorias medidas como as que mais caíam em "Outros" ---
    "transportation": ("Transporte", "Serviços"),
    "shopping_center": ("Shopping Center", "Comércio Varejista"),
    "industrial_equipment": ("Equipamentos Industriais", "Indústria"),
    "business_manufacturing_and_supply": ("Indústria e Suprimentos", "Indústria"),
    "manufacturing": ("Indústria", "Indústria"),
    "cosmetic_and_beauty_supplies": ("Cosméticos e Produtos de Beleza", "Comércio Varejista"),
    "arts_and_crafts": ("Artesanato", "Comércio Varejista"),
    "electronics": ("Eletrônicos", "Comércio Varejista"),
    "atms": ("Caixa Eletrônico", "Financeiro"),
    "atm": ("Caixa Eletrônico", "Financeiro"),
    "public_transportation": ("Transporte Público", "Serviços"),
    "warehouse": ("Depósito", "Indústria"),
    "logistics": ("Logística", "Serviços"),

    # --- v3.0.0: valores OSM admitidos pelo predicado ampliado ---
    "station": ("Estação", "Serviços"),
    "subway_entrance": ("Acesso de Metrô", "Serviços"),
    "bus_station": ("Terminal Rodoviário", "Serviços"),
    "aerodrome": ("Aeródromo", "Serviços"),
    "terminal": ("Terminal", "Serviços"),
    "hangar": ("Hangar", "Serviços"),
    "airfield": ("Campo de Aviação", "Público e Institucional"),
    "barracks": ("Quartel", "Público e Institucional"),
    "naval_base": ("Base Naval", "Público e Institucional"),
    "works": ("Planta Industrial", "Indústria"),
    "factory": ("Fábrica", "Indústria"),
    "industrial": ("Instalação Industrial", "Indústria"),
    "commercial": ("Imóvel Comercial", "Comércio Varejista"),
    "water_works": ("Estação de Tratamento de Água", "Público e Institucional"),
    "wastewater_plant": ("Estação de Tratamento de Esgoto", "Público e Institucional"),
    "water_tower": ("Reservatório Elevado", "Público e Institucional"),
    "storage_tank": ("Tanque de Armazenamento", "Indústria"),
    "substation": ("Subestação", "Público e Institucional"),
    "pumping_station": ("Estação Elevatória", "Público e Institucional"),
    "silo": ("Silo", "Indústria"),
    "carpenter": ("Marcenaria", "Serviços"),
    "electrician": ("Eletricista", "Serviços"),
    "plumber": ("Encanador", "Serviços"),
    "metal_construction": ("Serralheria", "Serviços"),
    "doctor": ("Consultório Médico", "Saúde"),
    "physiotherapist": ("Fisioterapia", "Saúde"),
    "psychotherapist": ("Psicoterapia", "Saúde"),
    "nutrition_counselling": ("Nutrição", "Saúde"),
    "sample_collection": ("Posto de Coleta", "Saúde"),
    "monument": ("Monumento", "Lazer e Cultura"),
    "memorial": ("Memorial", "Lazer e Cultura"),
    "castle": ("Castelo", "Lazer e Cultura"),
    "ruins": ("Ruínas", "Lazer e Cultura"),
}

# ---- tradutor por TOKENS (fallback) ----------------------------------------
# palavra(inglês) -> palavra(PT). Aplicado token a token quando a categoria
# inteira não está no mapa curado.
TOKENS = {
    "shop": "loja", "store": "loja", "market": "mercado", "supermarket": "supermercado",
    "restaurant": "restaurante", "bar": "bar", "cafe": "cafeteria", "coffee": "café",
    "bakery": "padaria", "service": "serviço", "services": "serviços",
    "repair": "oficina", "salon": "salão", "barber": "barbearia", "spa": "spa",
    "center": "centro", "centre": "centro", "clinic": "clínica", "hospital": "hospital",
    "pharmacy": "farmácia", "school": "escola", "university": "universidade",
    "college": "faculdade", "library": "biblioteca", "church": "igreja",
    "temple": "templo", "hotel": "hotel", "motel": "motel", "hostel": "hostel",
    "gym": "academia", "park": "parque", "museum": "museu", "theater": "teatro",
    "theatre": "teatro", "cinema": "cinema", "bank": "banco", "atm": "caixa eletrônico",
    "insurance": "seguros", "agency": "agência", "real": "imobiliário", "estate": "",
    "automotive": "automotivo", "auto": "automotivo", "car": "carro", "motorcycle": "moto",
    "tire": "borracharia", "parking": "estacionamento", "gas": "combustível",
    "station": "posto", "factory": "fábrica", "warehouse": "depósito",
    "office": "escritório", "government": "público", "police": "polícia",
    "fire": "bombeiros", "post": "correios", "dental": "odontológico",
    "dentist": "dentista", "doctor": "médico", "medical": "médico", "health": "saúde",
    "veterinary": "veterinário", "veterinarian": "veterinário", "pet": "pet",
    "beauty": "beleza", "hair": "cabelo", "nail": "unhas", "tattoo": "tatuagem",
    "clothing": "roupas", "shoe": "calçados", "furniture": "móveis",
    "hardware": "ferragens", "electronics": "eletrônicos", "book": "livros",
    "jewelry": "joias", "flower": "flores", "flowers": "flores", "gift": "presentes",
    "gifts": "presentes", "toy": "brinquedos", "sporting": "esportivos",
    "goods": "artigos", "cosmetics": "cosméticos", "department": "departamentos",
    "wholesale": "atacado", "building": "construção", "materials": "materiais",
    "mobile": "celular", "phone": "telefonia", "optician": "ótica",
    "liquor": "bebidas", "tobacco": "tabacaria", "fabric": "tecidos",
    "appliance": "eletrodomésticos", "laundry": "lavanderia", "locksmith": "chaveiro",
    "courier": "entregas", "delivery": "entregas", "moving": "mudanças",
    "advertising": "publicidade", "travel": "viagens", "employment": "emprego",
    "graphic": "gráfico", "designer": "designer", "print": "gráfica",
    "lawyer": "advocacia", "accountant": "contabilidade", "accounting": "contabilidade",
    "notary": "cartório", "funeral": "funerária", "tailor": "costura",
    "physiotherapist": "fisioterapia", "psychologist": "psicólogo",
    "laboratory": "laboratório", "lab": "laboratório", "optometrist": "optometrista",
    "primary": "fundamental", "high": "médio", "preschool": "infantil",
    "kindergarten": "creche", "language": "idiomas", "driving": "autoescola",
    "tutoring": "reforço", "music": "música", "vocational": "técnica",
    "fitness": "academia", "playground": "playground", "movie": "cinema",
    "art": "arte", "gallery": "galeria", "nightclub": "casa noturna",
    "stadium": "estádio", "sports": "esportivo", "club": "clube",
    "swimming": "piscina", "pool": "piscina", "event": "eventos", "space": "espaço",
    "dance": "dança", "martial": "artes marciais", "arts": "artes",
    "landmark": "marco", "historical": "histórico", "tourist": "turístico",
    "attraction": "atração", "catholic": "católica", "evangelical": "evangélica",
    "mosque": "mesquita", "synagogue": "sinagoga", "religious": "religiosa",
    "organization": "organização", "condominium": "condomínio",
    "apartment": "apartamentos", "property": "imóveis", "management": "administração",
    "manufacturer": "indústria", "industrial": "industrial", "company": "empresa",
    "distribution": "distribuição", "city": "cidade", "hall": "prefeitura",
    "courthouse": "fórum", "court": "fórum", "utility": "concessionária",
    "embassy": "embaixada", "community": "comunitário", "professional": "profissional",
    "rental": "locação", "rent": "aluguel", "security": "segurança",
    "home": "residencial", "house": "casa", "and": "e", "of": "de", "the": "",
    "snack": "lanchonete", "juice": "sucos", "candy": "doces", "ice": "sorvete",
    "cream": "sorvete", "food": "comida", "fast": "fast", "burger": "hambúrguer",
    "pizza": "pizzaria", "steakhouse": "churrascaria", "seafood": "frutos do mar",
    "butcher": "açougue", "grocery": "mercearia", "convenience": "conveniência",
    "drugstore": "drogaria", "drug": "drogaria",
    # reforço de cauda-longa
    "wedding": "casamento", "planning": "planejamento", "photography": "fotografia",
    "photographer": "fotógrafo", "consulting": "consultoria", "consultant": "consultoria",
    "software": "software", "technology": "tecnologia", "internet": "internet",
    "telecommunications": "telecomunicações", "telecom": "telecomunicações",
    "energy": "energia", "solar": "energia solar", "construction": "construção",
    "contractor": "construtora", "plumber": "encanador", "electrician": "eletricista",
    "painting": "pintura", "cleaning": "limpeza", "catering": "buffet",
    "florist": "floricultura", "supplies": "suprimentos", "supply": "suprimentos",
    "equipment": "equipamentos", "instrument": "instrumentos", "instruments": "instrumentos",
    "musical": "musical", "studio": "estúdio", "workshop": "oficina",
    "warehouse": "depósito", "logistics": "logística", "transport": "transporte",
    "transportation": "transporte", "freight": "fretes", "import": "importação",
    "export": "exportação", "trade": "comércio", "trading": "comércio",
    "distributor": "distribuidora", "supplier": "fornecedor", "industry": "indústria",
    "association": "associação", "foundation": "fundação", "institute": "instituto",
    "cooperative": "cooperativa", "union": "sindicato", "center": "centro",
    "clinic": "clínica", "care": "cuidados", "daycare": "creche", "nursery": "berçário",
    "salon": "salão", "esthetics": "estética", "aesthetic": "estética",
    "wellness": "bem-estar", "massage": "massagem", "yoga": "yoga", "pilates": "pilates",
    "store": "loja", "boutique": "boutique", "outlet": "outlet", "mall": "shopping",
    "shopping": "shopping", "plaza": "praça", "kiosk": "quiosque", "stand": "banca",
}

# token -> segmento (palpite de segmento no fallback)
SEG_TOKENS = {
    "restaurante": "Alimentação", "padaria": "Alimentação", "bar": "Alimentação",
    "cafeteria": "Alimentação", "lanchonete": "Alimentação", "pizzaria": "Alimentação",
    "loja": "Comércio Varejista", "mercado": "Comércio Varejista",
    "supermercado": "Comércio Varejista", "atacado": "Comércio Varejista",
    "farmácia": "Saúde", "drogaria": "Saúde", "clínica": "Saúde", "hospital": "Saúde",
    "médico": "Saúde", "dentista": "Saúde", "laboratório": "Saúde", "veterinário": "Saúde",
    "escola": "Educação", "universidade": "Educação", "faculdade": "Educação",
    "biblioteca": "Educação", "salão": "Beleza e Estética", "barbearia": "Beleza e Estética",
    "tatuagem": "Beleza e Estética", "oficina": "Automotivo", "estacionamento": "Automotivo",
    "borracharia": "Automotivo", "banco": "Financeiro", "seguros": "Financeiro",
    "hotel": "Hospedagem", "motel": "Hospedagem", "academia": "Lazer e Cultura",
    "parque": "Lazer e Cultura", "cinema": "Lazer e Cultura", "igreja": "Religioso",
    "templo": "Religioso", "imobiliário": "Imobiliário", "condomínio": "Imobiliário",
    "fábrica": "Indústria", "depósito": "Indústria", "público": "Público e Institucional",
    "correios": "Público e Institucional", "serviço": "Serviços", "serviços": "Serviços",
    "agência": "Serviços", "advocacia": "Serviços", "contabilidade": "Serviços",
}


# topo da hierarquia FSQ -> segmento (fallback de segmento quando a folha não bate)
FSQ_TOPO = {
    "dining and drinking": "Alimentação",
    "retail": "Comércio Varejista",
    "business and professional services": "Serviços",
    "health and medicine": "Saúde",
    "community and government": "Público e Institucional",
    "landmarks and outdoors": "Lazer e Cultura",
    "arts and entertainment": "Lazer e Cultura",
    "sports and recreation": "Lazer e Cultura",
    "travel and transportation": "Serviços",
    "event": "Lazer e Cultura",
}


def _titlecase(s):
    pequenas = {"e", "de", "da", "do", "a", "o"}
    out = []
    for i, w in enumerate(s.split()):
        out.append(w if (w in pequenas and i > 0) else (w[:1].upper() + w[1:]))
    return " ".join(out)


def _fallback(cat):
    """Traduz por tokens uma categoria não-curada e infere segmento."""
    raw = re.sub(r"[_\-/>]+", " ", str(cat)).lower().strip()
    palavras = [w for w in raw.split() if w]
    pt = []
    seg = "Outros"
    for w in palavras:
        t = TOKENS.get(w, w)
        if t:
            pt.append(t)
            if seg == "Outros" and t in SEG_TOKENS:
                seg = SEG_TOKENS[t]
    label = _titlecase(" ".join(pt)) if pt else _titlecase(raw)
    return label or "Não Classificado", seg


def traduzir_fsq(label):
    """Rótulo hierárquico FSQ ('Topo > ... > Folha') -> (categoria_pt, segmento).
    Folha define o nome PT; topo garante o segmento quando a folha não é curada."""
    if not label:
        return "Não Informado", "Outros"
    parts = [p.strip() for p in str(label).split(">") if p.strip()]
    leaf = parts[-1] if parts else str(label)
    top = parts[0].lower() if parts else ""
    key = leaf.lower().replace(" ", "_").replace("'", "").replace("&", "e")
    if key in CATEGORIAS:
        return CATEGORIAS[key]
    pt, seg = _fallback(leaf)
    if seg == "Outros":
        seg = FSQ_TOPO.get(top, "Outros")
    return pt, seg


# ---- topo da taxonomia do OVERTURE -> segmento A2L -------------------------
# A categoria primária do Overture cai fora do mapa curado em ~23-30% das linhas,
# mas `taxonomy.hierarchy` vem preenchida em 96,4% delas. Subir a hierarquia até um
# nível conhecido resolve a maior parte sem ampliar o léxico manualmente.
OV_TOPO = {
    "eat_and_drink": "Alimentação",
    "retail": "Comércio Varejista",
    "accommodation": "Hospedagem",
    "automotive": "Automotivo",
    "beauty_and_spa": "Beleza e Estética",
    "health_and_medical": "Saúde",
    "education": "Educação",
    "financial_service": "Financeiro",
    "professional_services": "Serviços",
    "business_to_business": "Serviços",
    "home_service": "Serviços",
    "mass_media": "Serviços",
    "travel": "Serviços",
    "transportation": "Serviços",
    "public_service_and_government": "Público e Institucional",
    "religious_organization": "Religioso",
    "real_estate": "Imobiliário",
    "attractions_and_activities": "Lazer e Cultura",
    "active_life": "Lazer e Cultura",
    "arts_and_entertainment": "Lazer e Cultura",
    "sports_and_recreation": "Lazer e Cultura",
    "private_establishments_and_corporates": "Serviços",
    "structure_and_geography": "Outros",
}

_SEP_HIER = re.compile(r"[;>|]")


def niveis_hierarquia(hier):
    """Normaliza a hierarquia da fonte numa lista de chaves, do topo para a folha.

    Aceita o que o achatamento produz: lista, string separada por ';' (listas de
    escalares viram ';'-joined) ou o rótulo FSQ separado por '>'."""
    if hier is None:
        return []
    if isinstance(hier, float) and hier != hier:
        return []
    if isinstance(hier, (list, tuple, set)):
        partes = [str(x) for x in hier]
    else:
        s = str(hier).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return []
        partes = _SEP_HIER.split(s)
    out = []
    for p in partes:
        k = p.strip().lower().replace(" ", "_").replace("'", "").replace("&", "e")
        if k and k not in out:
            out.append(k)
    return out


def _da_hierarquia(niveis):
    """(pt, segmento) a partir da hierarquia: nível curado mais específico vence;
    na falta dele, o topo define o segmento. Ordem topo->folha ou folha->topo: a
    busca cobre as duas pontas, então não depende da convenção da release."""
    especificos = [k for k in niveis if k not in OV_TOPO]
    for k in reversed(especificos):                  # mais específico primeiro
        if k in CATEGORIAS:
            return CATEGORIAS[k]
    for k in niveis:                                 # topo em qualquer ponta
        if k in OV_TOPO:
            return None, OV_TOPO[k]
    return None, None


def traduzir(cat, hier=None):
    """categoria -> (categoria_pt, segmento). Detecta hierarquia FSQ ('>'). Nunca None.

    `hier` é a hierarquia da fonte (Overture `taxonomy.hierarchy`, por ex.): só é
    consultada quando a categoria primária não resolve o segmento."""
    if not cat and not hier:
        return "Não Informado", "Outros"
    if cat and ">" in str(cat):             # rótulo hierárquico FSQ
        pt, seg = traduzir_fsq(cat)
    elif cat:
        c = str(cat).strip().lower()
        pt, seg = CATEGORIAS[c] if c in CATEGORIAS else _fallback(c)
    else:
        pt, seg = "Não Informado", "Outros"
    if seg != "Outros":
        return pt, seg
    pt_h, seg_h = _da_hierarquia(niveis_hierarquia(hier))
    if pt_h and pt in ("Não Informado", "Não Classificado"):
        pt = pt_h
    if seg_h:
        seg = seg_h
    elif pt_h:
        pt = pt if pt not in ("Não Informado", "Não Classificado") else pt_h
    return pt, seg


if __name__ == "__main__":
    for c in ["bakery", "beauty_salon", "automotive_repair", "pizza_restaurant",
              "home_security", "rental_service", "landmark_and_historical_building",
              "wedding_planning_service", "musical_instrument_store", "xyz_unknown_shop"]:
        print(f"{c:38} -> {traduzir(c)}")
