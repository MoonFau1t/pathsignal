import re
import unittest
from dataclasses import replace

from src.source_monitoring.entity_type_ontology import (
    ENTITY_TYPE_JUSTIFICATIONS,
    ENTITY_TYPE_ONTOLOGY_VERSION,
    PROHIBITED_ENTITY_TYPE_CODES,
    entity_type_codes,
    get_entity_type_ontology,
    normalize_entity_type_alias,
    normalize_entity_type_code,
    resolve_entity_type_code,
    validate_entity_type_ontology,
)


class EntityTypeOntologyTests(unittest.TestCase):
    def test_ontology_codes_are_unique_and_stable(self):
        codes = entity_type_codes()

        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(len(codes), 21)
        self.assertEqual(
            codes,
            (
                "technology_company",
                "ai_native_company",
                "enterprise_software_company",
                "industrial_technology_company",
                "growth_stage_company",
                "venture_capital_firm",
                "corporate_venture_capital",
                "private_equity_or_growth_equity_firm",
                "portfolio_company",
                "startup_accelerator",
                "venture_studio",
                "management_consulting_firm",
                "technology_consulting_firm",
                "boutique_investment_bank_or_fa",
                "research_institute",
                "policy_think_tank",
                "industry_association",
                "government_or_regulatory_body",
                "professional_media",
                "investment_data_provider",
                "recruiting_platform",
            ),
        )

    def test_definitions_are_non_empty_and_versioned(self):
        ontology = get_entity_type_ontology()
        before = [entity_type.to_dict() for entity_type in ontology]
        self.assertEqual(validate_entity_type_ontology(ontology), ontology)
        self.assertEqual([entity_type.to_dict() for entity_type in ontology], before)

        for entity_type in get_entity_type_ontology():
            self.assertTrue(entity_type.display_name)
            self.assertTrue(entity_type.definition)
            self.assertTrue(entity_type.broader_group)
            self.assertGreaterEqual(len(entity_type.aliases), 2)
            self.assertGreaterEqual(len(entity_type.example_signal_domains), 2)
            self.assertEqual(entity_type.ontology_version, ENTITY_TYPE_ONTOLOGY_VERSION)

    def test_aliases_resolve_deterministically(self):
        self.assertEqual(resolve_entity_type_code("CVC"), "corporate_venture_capital")
        self.assertEqual(
            resolve_entity_type_code("corporate VC"),
            "corporate_venture_capital",
        )
        self.assertEqual(resolve_entity_type_code("VC firm"), "venture_capital_firm")
        self.assertEqual(
            resolve_entity_type_code("venture capital fund"),
            "venture_capital_firm",
        )
        self.assertEqual(
            resolve_entity_type_code("financial advisory boutique"),
            "boutique_investment_bank_or_fa",
        )
        self.assertEqual(resolve_entity_type_code("FA"), "boutique_investment_bank_or_fa")

    def test_canonical_code_normalization_stays_ascii(self):
        self.assertEqual(
            normalize_entity_type_code("Technology Company"),
            "technology_company",
        )
        self.assertEqual(
            normalize_entity_type_code("technology_company"),
            "technology_company",
        )
        self.assertEqual(normalize_entity_type_code("科技公司"), "")
        self.assertEqual(normalize_entity_type_code("AI原生公司"), "")

    def test_chinese_aliases_resolve_to_english_canonical_codes(self):
        cases = {
            "科技公司": "technology_company",
            "AI原生公司": "ai_native_company",
            "企业软件公司": "enterprise_software_company",
            "风险投资机构": "venture_capital_firm",
            "企业创投": "corporate_venture_capital",
            "成长股权基金": "private_equity_or_growth_equity_firm",
            "成长股权投资机构": "private_equity_or_growth_equity_firm",
            "被投企业": "portfolio_company",
            "战略咨询公司": "management_consulting_firm",
            "数字化转型咨询公司": "technology_consulting_firm",
            "精品投行": "boutique_investment_bank_or_fa",
            "政策智库": "policy_think_tank",
            "行业协会": "industry_association",
            "监管机构": "government_or_regulatory_body",
            "行业媒体": "professional_media",
            "投融资数据平台": "investment_data_provider",
            "招聘平台": "recruiting_platform",
            "人才招聘平台": "recruiting_platform",
            "招聘求职平台": "recruiting_platform",
        }

        for alias, expected_code in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(resolve_entity_type_code(alias), expected_code)

    def test_unicode_alias_normalization_preserves_chinese(self):
        expected_key = normalize_entity_type_alias("AI原生公司")

        self.assertEqual(normalize_entity_type_alias(" ＡＩ原生公司 "), expected_key)
        self.assertEqual(normalize_entity_type_alias("AI 原生公司"), expected_key)
        self.assertEqual(normalize_entity_type_alias("AI-原生公司"), expected_key)
        self.assertEqual(normalize_entity_type_alias("AI／原生公司"), expected_key)
        self.assertEqual(normalize_entity_type_alias("AI、原生公司"), expected_key)

        for alias in (" ＡＩ原生公司 ", "AI 原生公司", "AI-原生公司"):
            self.assertEqual(resolve_entity_type_code(alias), "ai_native_company")

    def test_case_punctuation_and_separator_normalization(self):
        self.assertEqual(
            resolve_entity_type_code("Technology Consulting Firm"),
            "technology_consulting_firm",
        )
        self.assertEqual(
            resolve_entity_type_code("private equity / growth equity firm"),
            "private_equity_or_growth_equity_firm",
        )

    def test_duplicate_english_aliases_raise_clear_error(self):
        ontology = list(get_entity_type_ontology())
        ontology[0] = replace(
            ontology[0],
            aliases=ontology[0].aliases + ("VC firm",),
        )

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate Entity Type aliases.*technology_company.*venture_capital_firm",
        ):
            validate_entity_type_ontology(tuple(ontology))

    def test_duplicate_chinese_aliases_raise_clear_error(self):
        ontology = list(get_entity_type_ontology())
        ontology[0] = replace(
            ontology[0],
            aliases=ontology[0].aliases + ("风险投资机构",),
        )

        with self.assertRaisesRegex(
            ValueError,
            "Duplicate Entity Type aliases.*technology_company.*venture_capital_firm",
        ):
            validate_entity_type_ontology(tuple(ontology))

    def test_ambiguous_aliases_do_not_silently_resolve(self):
        self.assertIsNone(resolve_entity_type_code("policy research organization"))
        self.assertIsNone(resolve_entity_type_code("产业资本"))
        self.assertIsNone(resolve_entity_type_code("研究院"))
        self.assertIsNone(resolve_entity_type_code("咨询机构"))
        self.assertIsNone(resolve_entity_type_code("成长基金"))
        self.assertIsNone(resolve_entity_type_code("风险创业工作室"))
        self.assertIsNone(resolve_entity_type_code("人才平台"))

    def test_no_acquisition_mechanisms_are_entity_types(self):
        codes = set(entity_type_codes())

        self.assertTrue(PROHIBITED_ENTITY_TYPE_CODES.isdisjoint(codes))
        for source_type in PROHIBITED_ENTITY_TYPE_CODES:
            self.assertIsNone(resolve_entity_type_code(source_type))

    def test_no_concrete_company_names_appear_as_codes(self):
        concrete_name_pattern = re.compile(
            r"openai|google|microsoft|sequoia|mckinsey|bcg|bain",
            re.IGNORECASE,
        )

        for code in entity_type_codes():
            self.assertIsNone(concrete_name_pattern.search(code))

    def test_all_ontology_types_are_justified_by_project_scope(self):
        self.assertEqual(set(entity_type_codes()), set(ENTITY_TYPE_JUSTIFICATIONS))
        for reason in ENTITY_TYPE_JUSTIFICATIONS.values():
            self.assertGreater(len(reason), 20)


if __name__ == "__main__":
    unittest.main()
