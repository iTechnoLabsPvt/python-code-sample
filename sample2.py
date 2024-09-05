from decimal import Decimal
from typing import Any, Dict, List
import requests
from clients.common import PriceInfo, TokenOverview
from custom_exceptions import InvalidSolanaAddress, InvalidTokens, NoPositionsError
from utils.helpers import is_solana_address

SOL_MINT = None

class DexScreenerClient:
    """
    Handler class to assist with all calls to DexScreener API
    """

    @staticmethod
    def _validate_token_address(token_address: str) -> None:
        """
        Validates token address to be a valid Solana address

        Args:
            token_address (str): Token address to validate

        Raises:
            NoPositionsError: If token address is empty
            InvalidSolanaAddress: If token address is not a valid Solana address
        """
        if not token_address:
            raise NoPositionsError('Token address is empty')

        if not is_solana_address(token_address):
            raise InvalidSolanaAddress(address=token_address)

    @staticmethod
    def _validate_token_addresses(token_addresses: List[str]) -> None:
        """
        Validates token addresses to be valid Solana addresses

        Args:
            token_addresses (List[str]): Token addresses to validate

        Raises:
            NoPositionsError: If token addresses are empty
            InvalidSolanaAddress: If any token address is not a valid Solana address
        """
        if not token_addresses:
            raise NoPositionsError('Token addresses are empty')

        for token_address in token_addresses:
            if not is_solana_address(token_address):
                raise InvalidSolanaAddress(address=token_address)

    @staticmethod
    def _validate_response(resp: requests.Response) -> None:
        """
        Validates response from API to be 200 (OK)

        Args:
            resp (requests.Response): Response from API

        Raises:
            InvalidTokens: If response is not 200
        """
        if resp.status_code != 200:
            raise InvalidTokens()

    def _call_api(self, token_address: str) -> Dict[str, Any]:
        """
        Calls DexScreener API for a single token

        Args:
            token_address (str): Token address for which to fetch data

        Returns:
            Dict[str, Any]: JSON response from API

        Raises:
            InvalidTokens: If response is not 200
            NoPositionsError: If token address is empty
            InvalidSolanaAddress: If token address is not a valid Solana address
        """
        self._validate_token_address(token_address=token_address)

        query_url = f'https://api.dexscreener.io/latest/dex/tokens/{token_address}'
        resp = requests.get(query_url)

        self._validate_response(resp=resp)
        return resp.json()

    def _call_api_bulk(self, token_addresses: List[str]) -> Dict[str, Any]:
        """
        Calls DexScreener API for multiple tokens

        Args:
            token_addresses (List[str]): Token addresses for which to fetch data

        Returns:
            Dict[str, Any]: JSON response from API

        Raises:
            InvalidTokens: If response is not 200
            NoPositionsError: If token addresses are empty
            InvalidSolanaAddress: If any token address is not a valid Solana address
        """
        self._validate_token_addresses(token_addresses=token_addresses)

        token_list = ','.join(token_addresses)
        query_url = f'https://api.dexscreener.io/latest/dex/tokens/{token_list}'
        resp = requests.get(query_url)

        self._validate_response(resp=resp)
        return resp.json()

    def fetch_prices_dex(self, token_addresses: List[str]) -> Dict[str, PriceInfo[Decimal, Decimal]]:
        """
        For a list of tokens, fetches their prices via multi API, ensuring each token has a price

        Args:
            token_addresses (List[str]): A list of tokens for which to fetch prices

        Returns:
            Dict[str, PriceInfo[Decimal, Decimal]]: Mapping of token to a named tuple PriceInfo with price and liquidity in Decimal
        """
        token_prices = {}
        tokens_crawled = []

        try:
            dex_token_list = self._call_api_bulk(token_addresses=token_addresses)
        except Exception as e:
            raise e
        else:
            for pair in dex_token_List['pairs']:
                base_token = pair['baseToken']['address']

                if base_token in tokens_crawled:
                    continue

                tokens_crawled.append(base_token)

                price = Decimal(pair['priceUsd'])
                liquidity = Decimal(pair['liquidity']['usd'])

                token_prices[base_token] = PriceInfo(price=price, liquidity=liquidity)

        return token_prices

    def fetch_token_overview(self, address: str) -> TokenOverview:
        """
        For a token, fetches its overview via Dex API

        Args:
            address (str): A token address for which to fetch the overview

        Returns:
            TokenOverview: Overview with various token information
        """
        try:
            token_pairs = self._call_api(token_address=address)
        except Exception as e:
            raise e
        else:
            overview = token_pairs['pairs'][0]
            return TokenOverview(**overview)

    @staticmethod
    def find_largest_pool_with_sol(token_pairs: List[dict], address: str) -> dict:
        """
        Finds the largest pool with SOL for a given token address

        Args:
            token_pairs (List[dict]): List of token pairs
            address (str): Token address

        Returns:
            dict: The largest pool with SOL for the given token address
        """
        max_liquidity_usd = -1
        max_entry = {}

        for entry in token_pairs:
            base_token_address = entry.get("baseToken", {}).get("address")
            quote_token_address = entry.get("quoteToken", {}).get("address")

            if base_token_address == address and quote_token_address == SOL_MINT:
                liquidity_usd = float(entry.get("liquidity", {}).get("usd", 0))
                if liquidity_usd > max_liquidity_usd:
                    max_liquidity_usd = liquidity_usd
                    max_entry = entry

        return max_entry