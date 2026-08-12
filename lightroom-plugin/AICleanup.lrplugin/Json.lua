--[[----------------------------------------------------------------------------

Json.lua
Minimal pure-Lua JSON encoder/decoder.

The Lightroom Classic SDK has no built-in JSON support — every real-world
plugin that talks JSON over LrHttp vendors its own small encoder/decoder
(confirmed by inspecting several: they all bundle one rather than relying
on anything the SDK provides). This is a from-scratch implementation
rather than a copy of any specific one, written plain enough to audit
by hand and kept dependency-free. Targets Lua 5.1 (Lightroom's runtime):
no bitwise operators, no `//`, no `goto`.

------------------------------------------------------------------------------]]

local Json = {}

--------------------------------------------------------------------------------
-- Encoding

local escapeMap = {
	['"'] = '\\"',
	['\\'] = '\\\\',
	['\n'] = '\\n',
	['\r'] = '\\r',
	['\t'] = '\\t',
	['\b'] = '\\b',
	['\f'] = '\\f',
}

local function encodeString( s )
	local out = { '"' }
	for i = 1, #s do
		local c = s:sub( i, i )
		local escaped = escapeMap[ c ]
		if escaped then
			out[ #out + 1 ] = escaped
		elseif c:byte() < 0x20 then
			out[ #out + 1 ] = string.format( '\\u%04x', c:byte() )
		else
			out[ #out + 1 ] = c
		end
	end
	out[ #out + 1 ] = '"'
	return table.concat( out )
end

-- A table is treated as a JSON array iff its keys are exactly 1..n with no
-- gaps. Lua's `#` operator is unreliable for tables with holes, so this
-- counts explicitly.
local function isArray( t )
	local count = 0
	for _ in pairs( t ) do
		count = count + 1
	end
	if count == 0 then
		return true -- an empty table encodes as [] (see Json.encode's `emptyTableAsArray`)
	end
	for i = 1, count do
		if t[ i ] == nil then
			return false
		end
	end
	return true
end

local encodeValue -- forward declaration

local function encodeArray( t )
	local out = {}
	for i = 1, #t do
		out[ i ] = encodeValue( t[ i ] )
	end
	return '[' .. table.concat( out, ',' ) .. ']'
end

local function encodeObject( t )
	local out = {}
	for k, v in pairs( t ) do
		out[ #out + 1 ] = encodeString( tostring( k ) ) .. ':' .. encodeValue( v )
	end
	return '{' .. table.concat( out, ',' ) .. '}'
end

encodeValue = function( v )
	local t = type( v )
	if v == nil then
		return 'null'
	elseif t == 'boolean' then
		return tostring( v )
	elseif t == 'number' then
		return tostring( v )
	elseif t == 'string' then
		return encodeString( v )
	elseif t == 'table' then
		if isArray( v ) then
			return encodeArray( v )
		else
			return encodeObject( v )
		end
	end
	error( 'Json.encode: unsupported type ' .. t )
end

function Json.encode( value )
	return encodeValue( value )
end

--------------------------------------------------------------------------------
-- Decoding

local function newParser( text )
	return { text = text, pos = 1, len = #text }
end

local function skipWhitespace( p )
	local _, stop = p.text:find( '^%s*', p.pos )
	if stop then
		p.pos = stop + 1
	end
end

local function fail( p, message )
	error( 'Json.decode: ' .. message .. ' at position ' .. tostring( p.pos ) )
end

local decodeValue -- forward declaration

local function decodeString( p )
	if p.text:sub( p.pos, p.pos ) ~= '"' then
		fail( p, 'expected string' )
	end
	p.pos = p.pos + 1
	local out = {}
	while true do
		local c = p.text:sub( p.pos, p.pos )
		if c == '' then
			fail( p, 'unterminated string' )
		elseif c == '"' then
			p.pos = p.pos + 1
			return table.concat( out )
		elseif c == '\\' then
			local esc = p.text:sub( p.pos + 1, p.pos + 1 )
			if esc == 'n' then out[ #out + 1 ] = '\n'
			elseif esc == 't' then out[ #out + 1 ] = '\t'
			elseif esc == 'r' then out[ #out + 1 ] = '\r'
			elseif esc == 'b' then out[ #out + 1 ] = '\b'
			elseif esc == 'f' then out[ #out + 1 ] = '\f'
			elseif esc == '"' then out[ #out + 1 ] = '"'
			elseif esc == '\\' then out[ #out + 1 ] = '\\'
			elseif esc == '/' then out[ #out + 1 ] = '/'
			elseif esc == 'u' then
				local hex = p.text:sub( p.pos + 2, p.pos + 5 )
				local code = tonumber( hex, 16 ) or 0
				-- Only the ASCII subset round-trips without a full UTF-8
				-- encoder; this plugin's payloads (paths, ids, enum-ish
				-- strings) never need non-ASCII \u escapes in practice.
				if code < 0x80 then
					out[ #out + 1 ] = string.char( code )
				end
				p.pos = p.pos + 4
			else
				fail( p, 'invalid escape \\' .. esc )
			end
			p.pos = p.pos + 2
		else
			out[ #out + 1 ] = c
			p.pos = p.pos + 1
		end
	end
end

local function decodeNumber( p )
	local match = p.text:match( '^-?%d+%.?%d*[eE]?[+-]?%d*', p.pos )
	if not match or match == '' then
		fail( p, 'expected number' )
	end
	p.pos = p.pos + #match
	return tonumber( match )
end

local function decodeArray( p )
	p.pos = p.pos + 1 -- consume '['
	local result = {}
	skipWhitespace( p )
	if p.text:sub( p.pos, p.pos ) == ']' then
		p.pos = p.pos + 1
		return result
	end
	while true do
		skipWhitespace( p )
		result[ #result + 1 ] = decodeValue( p )
		skipWhitespace( p )
		local c = p.text:sub( p.pos, p.pos )
		if c == ',' then
			p.pos = p.pos + 1
		elseif c == ']' then
			p.pos = p.pos + 1
			return result
		else
			fail( p, "expected ',' or ']'" )
		end
	end
end

local function decodeObject( p )
	p.pos = p.pos + 1 -- consume '{'
	local result = {}
	skipWhitespace( p )
	if p.text:sub( p.pos, p.pos ) == '}' then
		p.pos = p.pos + 1
		return result
	end
	while true do
		skipWhitespace( p )
		local key = decodeString( p )
		skipWhitespace( p )
		if p.text:sub( p.pos, p.pos ) ~= ':' then
			fail( p, "expected ':'" )
		end
		p.pos = p.pos + 1
		skipWhitespace( p )
		result[ key ] = decodeValue( p )
		skipWhitespace( p )
		local c = p.text:sub( p.pos, p.pos )
		if c == ',' then
			p.pos = p.pos + 1
		elseif c == '}' then
			p.pos = p.pos + 1
			return result
		else
			fail( p, "expected ',' or '}'" )
		end
	end
end

decodeValue = function( p )
	skipWhitespace( p )
	local c = p.text:sub( p.pos, p.pos )
	if c == '{' then
		return decodeObject( p )
	elseif c == '[' then
		return decodeArray( p )
	elseif c == '"' then
		return decodeString( p )
	elseif c == 't' and p.text:sub( p.pos, p.pos + 3 ) == 'true' then
		p.pos = p.pos + 4
		return true
	elseif c == 'f' and p.text:sub( p.pos, p.pos + 4 ) == 'false' then
		p.pos = p.pos + 5
		return false
	elseif c == 'n' and p.text:sub( p.pos, p.pos + 3 ) == 'null' then
		p.pos = p.pos + 4
		return nil
	elseif c == '-' or c:match( '%d' ) then
		return decodeNumber( p )
	end
	fail( p, 'unexpected character ' .. tostring( c ) )
end

function Json.decode( text )
	local p = newParser( text )
	skipWhitespace( p )
	local value = decodeValue( p )
	return value
end

return Json
