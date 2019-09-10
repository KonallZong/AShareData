import datetime as dt
import json
import logging
from importlib.resources import open_text
from typing import Sequence, List, Callable, Union

import pandas as pd
import sqlalchemy as sa
from cached_property import cached_property

from AShareData import utils
from AShareData.constants import INDUSTRY_LEVEL


class SQLDBReader(object):
    def __init__(self, engine: sa.engine.Engine) -> None:
        """
        SQL Database Reader

        :param engine: sqlalchemy engine
        """
        self.engine = engine

    @cached_property
    def calendar(self) -> List[dt.datetime]:
        return utils.get_calendar(self.engine)

    @cached_property
    def stocks(self) -> List[str]:
        return utils.get_stocks(self.engine)

    def get_listed_stock(self, date: utils.DateType = dt.date.today()) -> List[str]:
        date = utils.date_type2datetime(date)
        raw_data = pd.read_sql_table('股票上市退市', self.engine)
        data = raw_data.loc[raw_data.DateTime <= date, :]
        return sorted(list(set(data.loc[data['上市状态'] == 1, 'ID'].values.tolist()) -
                           set(data.loc[data['上市状态'] == 0, 'ID'].values.tolist())))

    def get_factor(self, table_name: str, factor_name: str, ffill: bool = False,
                   start_date: utils.DateType = None, end_date: utils.DateType = None,
                   stock_list: Sequence[str] = None) -> Union[pd.DataFrame, pd.Series]:
        table_name = table_name.lower()
        primary_keys = self._check_args_and_get_primary_keys(table_name, factor_name)

        query_columns = primary_keys + [factor_name]
        logging.debug('开始读取数据.')
        # todo: this takes way too long for a large db
        df = pd.read_sql_table(table_name, self.engine, index_col=primary_keys, columns=query_columns)
        logging.debug('数据读取完成.')
        df.sort_index()
        if isinstance(df.index, pd.MultiIndex):
            df = df.unstack().droplevel(None, axis=1)
            df = self._conform_df(df, ffill=ffill, start_date=start_date, end_date=end_date, stock_list=stock_list)
            # name may not survive pickling
            df.name = factor_name
        return df

    def get_financial_factor(self, table_name: str, factor_name: str, agg_func: Callable,
                             start_date: utils.DateType = None, end_date: utils.DateType = None,
                             stock_list: Sequence[str] = None, yearly: bool = True) -> pd.DataFrame:
        table_name = table_name.lower()
        primary_keys = self._check_args_and_get_primary_keys(table_name, factor_name)
        query_columns = primary_keys + [factor_name]

        data = pd.read_sql_table(table_name, self.engine, columns=query_columns)
        if yearly:
            data = data.loc[lambda x: x['报告期'].dt.month == 12, :]

        storage = []
        all_secs = set(data.ID.unique().tolist())
        if stock_list:
            all_secs = all_secs & set(stock_list)
        for sec_id in all_secs:
            id_data = data.loc[data.ID == sec_id, :]
            dates = id_data.DateTime.dt.to_pydatetime().tolist()
            dates = sorted(list(set(dates)))
            for date in dates:
                date_id_data = id_data.loc[data.DateTime <= date, :]
                each_date_data = date_id_data.groupby('报告期', as_index=False).last()
                each_date_data.set_index(['DateTime', 'ID', '报告期'], inplace=True)
                output_data = each_date_data.apply({factor_name: agg_func})
                output_data.index = pd.MultiIndex.from_tuples([(date, sec_id)], names=['DateTime', 'ID'])
                storage.append(output_data)

        df = pd.concat(storage)
        df = df.unstack().droplevel(None, axis=1)
        df = self._conform_df(df, False, start_date, end_date, stock_list)
        # name may not survive pickling
        df.name = factor_name
        return df

    def get_industry(self, provider: str, level: int, translation_json_loc: str = None,
                     start_date: utils.DateType = None, end_date: utils.DateType = None,
                     stock_list: Sequence[str] = None) -> pd.DataFrame:
        assert 0 < level <= INDUSTRY_LEVEL[provider], f'{provider}行业没有{level}级'

        table_name = f'{provider}行业'
        industry_col_name = '行业名称'
        primary_keys = ['DateTime', 'ID']
        query_columns = primary_keys + [industry_col_name]
        logging.debug('开始读取数据.')
        df = pd.read_sql_table(table_name, self.engine, index_col=primary_keys, columns=query_columns)
        logging.debug('数据读取完成.')

        if level != INDUSTRY_LEVEL[provider]:
            if translation_json_loc is None:
                from AShareData import data
                translation = json.load(open_text(data, 'industry.json'))
            else:
                with open(translation_json_loc, 'r', encoding='utf-8') as f:
                    translation = json.load(f)

            new_translation = {}
            for key, value in translation[table_name].items():
                new_translation[key] = value[f'level_{level}']
            df = df[industry_col_name].map(new_translation)

        df = df.unstack()
        df = self._conform_df(df, True, start_date, end_date, stock_list)
        return df

    def get_all_financial_data(self, sec_id: str, period: str) -> pd.DataFrame:
        storage = []
        tables = ['合并资产负债表', '合并现金流量表', '合并利润表']
        for table in tables:
            storage.append(pd.read_sql(f'SELECT * FROM {table} WHERE ID = "{sec_id}" AND 报告期 = "{period}"',
                                       con=self.engine, index_col=['DateTime', 'ID', '报告期']))
        all_data = pd.concat(storage, axis=1)
        return all_data

    # helper functions
    def _check_args_and_get_primary_keys(self, table_name: str, factor_name: str) -> List[str]:
        meta = sa.MetaData(bind=self.engine)
        meta.reflect()

        assert table_name in meta.tables.keys(), f'数据库中不存在表 {table_name}'

        columns = [it.name for it in meta.tables[table_name].c]
        assert factor_name in columns, f'表 {table_name} 中不存在 {factor_name} 列'

        primary_keys = [it for it in ['DateTime', 'ID', '报告期'] if it in columns]
        return primary_keys

    def _conform_df(self, df, ffill: bool = False,
                    start_date: utils.DateType = None, end_date: utils.DateType = None,
                    stock_list: Sequence[str] = None) -> pd.DataFrame:
        if ffill:
            first_timestamp = df.index.get_level_values(0).min()
            date_list = utils.select_dates(self.calendar, first_timestamp, end_date)
            df = df.reindex(date_list[:-1]).ffill()
            df = df.loc[start_date:, :]
        else:
            date_list = utils.select_dates(self.calendar, start_date, end_date)
            df = df.reindex(date_list[:-1])

        if not stock_list:
            stock_list = self.stocks
        df = df.reindex(stock_list, axis=1)
        return df
