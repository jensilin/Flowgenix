"""Generate flows/flow-pm-omes-sftp-nbi.json with embedded Groovy scripts."""
from __future__ import annotations

import json
from pathlib import Path

PARSE_PM_SCRIPT = r"""
import groovy.json.JsonSlurper
import org.apache.nifi.processor.io.OutputStreamCallback

def flowFile = session.get()
if (!flowFile) return

def propVal = { String n, String dflt ->
    try {
        def p = context.getProperty(n)
        if (p == null) return dflt
        def v = p.isSet() ? p.getValue() : null
        return (v == null || v.toString().trim().isEmpty()) ? dflt : v.toString()
    } catch (Exception e) { return dflt }
}

def loadJsonFile = { String baseDir, String fileName ->
    try {
        File f = new File(baseDir, fileName)
        if (!f.exists()) return [:]
        def parsed = new JsonSlurper().parseText(f.getText('UTF-8'))
        return (parsed instanceof Map) ? new LinkedHashMap(parsed) : [:]
    } catch (Exception e) {
        log.warn('Could not load lookup file ' + fileName + ': ' + e)
        return [:]
    }
}

String lookupDir = propVal('Lookup Directory', '')
Map nodeMap = loadJsonFile(lookupDir, 'node-map.json')
Map categoryMap = loadJsonFile(lookupDir, 'category-map.json')
Map counterMap = loadJsonFile(lookupDir, 'counter-map.json')

def csvCell = { v ->
    String s = (v == null) ? '' : v.toString()
    return (s.contains(',') || s.contains('"') || s.contains('\n')) ? '"' + s.replace('"', '""') + '"' : s
}

try {
    String text = new String(session.read(flowFile).bytes, 'UTF-8')
    String generationTime = ''

    def tsMatcher = (text =~ /(?is)(?:generated|generation|collection[ _-]?time)\s*[:=]\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})/)
    if (tsMatcher.find()) generationTime = tsMatcher.group(1).trim()
    if (!generationTime) {
        def lineTs = (text =~ /(?m)^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})/)
        if (lineTs.find()) generationTime = lineTs.group(1).trim()
    }
    if (!generationTime) generationTime = flowFile.getAttribute('file.lastModifiedTime') ?: ''

    String currentNode = ''
    String currentDn = ''
    List<String> rows = []
    rows.add('generationTime,nodeId,nodeAlias,rawCounterName,rawCounterValue,dnComponents,categoryHint')

    text.split(/\r?\n/).each { String rawLine ->
        String line = rawLine == null ? '' : rawLine.trim()
        if (!line || line.startsWith('#')) return
        if (line == '---' || line == '***') return

        def bracket = (line =~ /^\[(.+)\]$/)
        if (bracket.matches()) {
            currentNode = bracket.group(1).trim()
            return
        }
        def nodeLine = (line =~ /(?i)^(?:node|ne)\s*[:=]\s*(\S+)/)
        if (nodeLine.matches()) {
            currentNode = nodeLine.group(1).trim()
            return
        }
        def dnLine = (line =~ /(?i)^(?:dn|distinguished[ _-]?name)\s*[:=]\s*(.+)$/)
        if (dnLine.matches()) {
            currentDn = dnLine.group(1).trim()
            return
        }

        String nodeId = currentNode
        String counter = ''
        String value = ''
        String dn = currentDn

        def kv = (line =~ /^([A-Za-z][A-Za-z0-9_.-]*)\s*[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$/)
        if (kv.matches()) {
            counter = kv.group(1).trim()
            value = kv.group(2).trim()
        } else {
            def parts = (line =~ /^(\S+)\s*[,;|]\s*(\S+)\s*[,;|]\s*(\S+)(?:\s*[,;|]\s*(.+))?$/)
            if (parts.matches()) {
                nodeId = parts.group(1).trim()
                counter = parts.group(2).trim()
                value = parts.group(3).trim()
                if (parts.groupCount() >= 4 && parts.group(4) != null) dn = parts.group(4).trim()
            }
        }
        if (!counter || !nodeId) return

        String alias = ''
        if (nodeMap.get(nodeId) instanceof Map) {
            alias = String.valueOf(((Map) nodeMap.get(nodeId)).get('alias') ?: '')
        }
        String categoryHint = categoryMap.containsKey(counter) ? String.valueOf(categoryMap.get(counter)) : ''
        if (!categoryHint && counterMap.containsKey(counter)) categoryHint = ''

        rows.add([
            generationTime, nodeId, alias, counter, value, dn, categoryHint
        ].collect(csvCell).join(','))
    }

    String csv = rows.join('\n')
    flowFile = session.write(flowFile, { o -> o.write(csv.getBytes('UTF-8')) } as OutputStreamCallback)
    Map attrs = new LinkedHashMap()
    attrs.put('mime.type', 'text/csv')
    attrs.put('pm.generation.time', generationTime)
    attrs.put('pm.parse.row.count', String.valueOf(Math.max(0, rows.size() - 1)))
    attrs.put('pm.lookup.dir', lookupDir)
    flowFile = session.putAllAttributes(flowFile, attrs)
    session.transfer(flowFile, REL_SUCCESS)
} catch (Exception e) {
    log.error('PM text parse failed: ' + e, e)
    flowFile = session.putAttribute(flowFile, 'pm.parse.error', String.valueOf(e.getMessage()))
    session.transfer(flowFile, REL_FAILURE)
}
""".strip()

NORMALIZE_SCRIPT = r"""
import groovy.json.JsonSlurper
import groovy.json.JsonOutput
import org.apache.nifi.processor.io.OutputStreamCallback

def flowFile = session.get()
if (!flowFile) return

def propVal = { String n, String dflt ->
    try {
        def p = context.getProperty(n)
        if (p == null) return dflt
        def v = p.isSet() ? p.getValue() : null
        return (v == null || v.toString().trim().isEmpty()) ? dflt : v.toString()
    } catch (Exception e) { return dflt }
}

def loadJsonFile = { String baseDir, String fileName ->
    try {
        File f = new File(baseDir, fileName)
        if (!f.exists()) return [:]
        def parsed = new JsonSlurper().parseText(f.getText('UTF-8'))
        return (parsed instanceof Map) ? new LinkedHashMap(parsed) : [:]
    } catch (Exception e) {
        log.warn('Could not load lookup file ' + fileName + ': ' + e)
        return [:]
    }
}

String lookupDir = propVal('Lookup Directory', flowFile.getAttribute('pm.lookup.dir') ?: '')
Map nodeMap = loadJsonFile(lookupDir, 'node-map.json')
Map categoryMap = loadJsonFile(lookupDir, 'category-map.json')
Map counterMap = loadJsonFile(lookupDir, 'counter-map.json')
String dnPrefix = propVal('DN Prefix', 'ManagedElement=')

def isNullish = { v ->
    if (v == null) return true
    String s = v.toString().trim()
    return s.isEmpty() || s.equalsIgnoreCase('null') || s.equalsIgnoreCase('na') || s == '-'
}

def buildDn = { String nodeNf, String dnComponents ->
    if (dnComponents != null && !dnComponents.trim().isEmpty()) {
        String dn = dnComponents.trim()
        if (!dn.contains('ManagedElement=') && !dn.startsWith(dnPrefix)) {
            return dnPrefix + nodeNf + ',' + dn
        }
        return dn
    }
    return dnPrefix + nodeNf
}

try {
    String raw = new String(session.read(flowFile).bytes, 'UTF-8')
    def parsed = raw.trim().isEmpty() ? [] : new JsonSlurper().parseText(raw)
    List records = (parsed instanceof List) ? (List) parsed : [parsed]

    List out = []
    records.each { rec ->
        if (!(rec instanceof Map)) return
        Map row = (Map) rec
        String nodeId = String.valueOf(row.get('nodeId') ?: row.get('node') ?: '').trim()
        String rawCounter = String.valueOf(row.get('rawCounterName') ?: row.get('counterName') ?: '').trim()
        def rawValue = row.get('rawCounterValue')
        if (rawValue == null) rawValue = row.get('counterValue')
        if (isNullish(rawValue)) return

        String nf = nodeId
        if (nodeMap.get(nodeId) instanceof Map) {
            String mapped = String.valueOf(((Map) nodeMap.get(nodeId)).get('nf') ?: '').trim()
            if (!mapped.isEmpty()) nf = mapped
        }

        String category = String.valueOf(row.get('categoryHint') ?: '').trim()
        if (category.isEmpty() && categoryMap.containsKey(rawCounter)) {
            category = String.valueOf(categoryMap.get(rawCounter))
        }
        if (category.isEmpty()) category = 'GENERIC'

        String nbiName = counterMap.containsKey(rawCounter) ? String.valueOf(counterMap.get(rawCounter)) : rawCounter
        String dn = buildDn(nf, String.valueOf(row.get('dnComponents') ?: row.get('dn') ?: ''))
        String collectionTime = String.valueOf(row.get('generationTime') ?: flowFile.getAttribute('pm.generation.time') ?: '')

        Map normalized = new LinkedHashMap()
        normalized.put('collectionTime', collectionTime)
        normalized.put('nodeId', nodeId)
        normalized.put('networkFunction', nf)
        normalized.put('category', category)
        normalized.put('rawCounterName', rawCounter)
        normalized.put('nbiCounterName', nbiName)
        normalized.put('metricName', category + ':' + nbiName)
        normalized.put('metricValue', rawValue)
        normalized.put('dn', dn)
        out.add(normalized)
    }

    byte[] body = JsonOutput.toJson(out).getBytes('UTF-8')
    flowFile = session.write(flowFile, { o -> o.write(body) } as OutputStreamCallback)
    Map attrs = new LinkedHashMap()
    attrs.put('mime.type', 'application/json')
    attrs.put('pm.generation.time', flowFile.getAttribute('pm.generation.time') ?: '')
    attrs.put('pm.normalized.count', String.valueOf(out.size()))
    attrs.put('pm.lookup.dir', lookupDir)
    flowFile = session.putAllAttributes(flowFile, attrs)
    session.transfer(flowFile, REL_SUCCESS)
} catch (Exception e) {
    log.error('PM normalize/filter failed: ' + e, e)
    flowFile = session.putAttribute(flowFile, 'pm.normalize.error', String.valueOf(e.getMessage()))
    session.transfer(flowFile, REL_FAILURE)
}
""".strip()

BUILD_XML_SCRIPT = r"""
import groovy.json.JsonSlurper
import org.apache.nifi.processor.io.OutputStreamCallback

def flowFile = session.get()
if (!flowFile) return

def propVal = { String n, String dflt ->
    try {
        def p = context.getProperty(n)
        if (p == null) return dflt
        def v = p.isSet() ? p.getValue() : null
        return (v == null || v.toString().trim().isEmpty()) ? dflt : v.toString()
    } catch (Exception e) { return dflt }
}

def xmlEscape = { String s ->
    if (s == null) return ''
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&apos;')
}

try {
    String raw = new String(session.read(flowFile).bytes, 'UTF-8')
    def parsed = raw.trim().isEmpty() ? [] : new JsonSlurper().parseText(raw)
    List records = (parsed instanceof List) ? (List) parsed : [parsed]

    String generationTime = flowFile.getAttribute('pm.generation.time') ?: ''
    if (generationTime.isEmpty() && !records.isEmpty() && records[0] instanceof Map) {
        generationTime = String.valueOf(((Map) records[0]).get('collectionTime') ?: '')
    }
    String compactTs = generationTime.replaceAll('[^0-9]', '')
    if (compactTs.length() < 8) compactTs = String.valueOf(System.currentTimeMillis())

    String ffv = propVal('File Format Version', '32.435 V10.0')
    String vendor = propVal('Vendor Name', 'OMeS')
    String gp = propVal('Granularity Period', '900')

    Map grouped = new LinkedHashMap()
    records.each { rec ->
        if (!(rec instanceof Map)) return
        Map row = (Map) rec
        String dn = String.valueOf(row.get('dn') ?: '').trim()
        if (dn.isEmpty()) return
        if (!grouped.containsKey(dn)) grouped.put(dn, [])
        ((List) grouped.get(dn)).add(row)
    }

    StringBuilder xml = new StringBuilder()
    xml.append('<?xml version="1.0" encoding="UTF-8"?>\n')
    xml.append('<OMeS>\n')
    xml.append('  <PMSetup>\n')
    xml.append('    <fileFormatVersion>').append(xmlEscape(ffv)).append('</fileFormatVersion>\n')
    xml.append('    <vendorName>').append(xmlEscape(vendor)).append('</vendorName>\n')
    xml.append('    <generationTime>').append(xmlEscape(compactTs)).append('</generationTime>\n')
    xml.append('    <granularityPeriod>').append(xmlEscape(gp)).append('</granularityPeriod>\n')
    xml.append('  </PMSetup>\n')

    grouped.each { dnKey, metricRows ->
        String dn = String.valueOf(dnKey)
        xml.append('  <PMMOResult dn="').append(xmlEscape(dn)).append('">\n')
        ((List) metricRows).each { mr ->
            if (!(mr instanceof Map)) return
            Map m = (Map) mr
            String metricName = String.valueOf(m.get('metricName') ?: m.get('nbiCounterName') ?: '')
            String metricValue = String.valueOf(m.get('metricValue') ?: '')
            xml.append('    <metric name="').append(xmlEscape(metricName)).append('" value="')
            xml.append(xmlEscape(metricValue)).append('"/>\n')
        }
        xml.append('  </PMMOResult>\n')
    }
    xml.append('</OMeS>\n')

    flowFile = session.write(flowFile, { o -> o.write(xml.toString().getBytes('UTF-8')) } as OutputStreamCallback)
    Map attrs = new LinkedHashMap()
    attrs.put('mime.type', 'application/xml')
    attrs.put('pm.generation.time', generationTime)
    attrs.put('pm.generation.compact', compactTs)
    attrs.put('pm.mor.count', String.valueOf(grouped.size()))
    attrs.put('pm.metric.count', String.valueOf(records.size()))
    flowFile = session.putAllAttributes(flowFile, attrs)
    session.transfer(flowFile, REL_SUCCESS)
} catch (Exception e) {
    log.error('OMeS XML build failed: ' + e, e)
    flowFile = session.putAttribute(flowFile, 'pm.xml.error', String.valueOf(e.getMessage()))
    session.transfer(flowFile, REL_FAILURE)
}
""".strip()

LOOKUP_DIR = r"C:\Users\ravgs\nifi-cursor-automation\data\pm-lookup"
TRUSTSTORE = r"C:\Program Files\Java\jdk-21\lib\security\cacerts"

spec = {
    "processGroupName": "PM OMeS SFTP to NBI Adaptor",
    "position": {"x": 200, "y": 200},
    "start": True,
    "replaceExisting": True,
    "nifiVersion": "1.25.0",
    "comments": "Poll PM statistics via SFTP every 15 minutes, parse unstructured text to CSV, normalize with external lookup maps, build OMeS XML grouped by DN, and POST to central NBI.",
    "parameterContext": "PmOmesSftpNbiContext",
    "parameterContexts": [
        {
            "name": "PmOmesSftpNbiContext",
            "description": "SFTP source, lookup directory, and NBI endpoint parameters",
            "parameters": [
                {"name": "sftpHost", "value": "127.0.0.1", "sensitive": False},
                {"name": "sftpPort", "value": "22", "sensitive": False},
                {"name": "sftpUsername", "value": "pmuser", "sensitive": False},
                {"name": "sftpPassword", "value": "changeme", "sensitive": True},
                {"name": "sftpRemoteDir", "value": "/pm/statistics", "sensitive": False},
                {"name": "lookupDir", "value": LOOKUP_DIR, "sensitive": False},
                {"name": "nbiEndpoint", "value": "https://127.0.0.1:9443/nbi/pm/omes/upload", "sensitive": False},
            ],
        }
    ],
    "labels": [
        {
            "text": "Stage 1: GetSFTP polls remote PM statistics every 15 min (parameterized host/port/credentials/path).",
            "x": 40,
            "y": 20,
            "width": 900,
            "height": 50,
        },
        {
            "text": "Stage 2-4: Groovy parse -> ConvertRecord(CSV->JSON) -> Groovy normalize/filter (external lookup maps).",
            "x": 40,
            "y": 80,
            "width": 900,
            "height": 50,
        },
        {
            "text": "Stage 5-7: Build OMeS XML (PMSetup + PMMOResult by DN) -> UpdateAttribute filename -> InvokeHTTP POST to NBI.",
            "x": 40,
            "y": 140,
            "width": 900,
            "height": 50,
        },
    ],
    "controllerServices": [
        {
            "name": "PmCsvReader",
            "type": "org.apache.nifi.csv.CSVReader",
            "bundle": {
                "group": "org.apache.nifi",
                "artifact": "nifi-record-serialization-services-nar",
                "version": "1.25.0",
            },
            "properties": {
                "schema-access-strategy": "infer-schema",
                "csv-reader-csv-parser": "commons-csv",
                "CSV Format": "custom",
                "Value Separator": ",",
                "Record Separator": "\\n",
                "Skip Header Line": "true",
                "Quote Character": '"',
                "Escape Character": "\\",
                "Trim Fields": "true",
                "csvutils-character-set": "UTF-8",
            },
        },
        {
            "name": "PmJsonWriter",
            "type": "org.apache.nifi.json.JsonRecordSetWriter",
            "bundle": {
                "group": "org.apache.nifi",
                "artifact": "nifi-record-serialization-services-nar",
                "version": "1.25.0",
            },
            "properties": {
                "Schema Write Strategy": "no-schema",
                "schema-access-strategy": "inherit-record-schema",
                "output-grouping": "output-array",
                "suppress-nulls": "never-suppress",
                "Pretty Print JSON": "false",
                "compression-format": "none",
            },
        },
        {
            "name": "NbiSslContext",
            "type": "org.apache.nifi.ssl.StandardSSLContextService",
            "bundle": {
                "group": "org.apache.nifi",
                "artifact": "nifi-ssl-context-service-nar",
                "version": "1.25.0",
            },
            "properties": {
                "SSL Protocol": "TLS",
                "Truststore Filename": TRUSTSTORE,
                "Truststore Password": "changeit",
                "Truststore Type": "JKS",
            },
        },
    ],
    "processors": [
        {
            "name": "GetSFTP",
            "type": "GetSFTP",
            "x": 80,
            "y": 280,
            "schedulingPeriod": "15 min",
            "executionNode": "PRIMARY",
            "properties": {
                "Hostname": "#{sftpHost}",
                "Port": "#{sftpPort}",
                "Username": "#{sftpUsername}",
                "Password": "#{sftpPassword}",
                "Remote Path": "#{sftpRemoteDir}",
                "File Filter": "[^\\.].*",
                "Search Recursively": "false",
                "follow-symlink": "false",
                "Ignore Dotted Files": "true",
                "Minimum File Age": "0 sec",
                "Minimum File Size": "0 B",
                "Delete Original": "false",
                "Keep Source File": "true",
                "Strict Host Key Checking": "false",
                "Send Keep Alive On Timeout": "true",
                "Use Compression": "false",
                "Connection Timeout": "30 sec",
                "Data Timeout": "60 sec",
            },
            "autoTerminated": ["not.found"],
        },
        {
            "name": "Parse PM Text To CSV",
            "type": "ExecuteGroovyScript",
            "x": 420,
            "y": 280,
            "properties": {
                "groovyx-failure-strategy": "transfer to failure",
                "groovyx-script-body": PARSE_PM_SCRIPT,
            },
            "dynamicProperties": {
                "Lookup Directory": "#{lookupDir}",
            },
            "autoTerminated": [],
        },
        {
            "name": "ConvertRecord",
            "type": "ConvertRecord",
            "x": 760,
            "y": 280,
            "properties": {
                "record-reader": "@PmCsvReader",
                "record-writer": "@PmJsonWriter",
                "include-zero-record-flowfiles": "false",
            },
            "autoTerminated": [],
        },
        {
            "name": "Normalize Map And Filter JSON",
            "type": "ExecuteGroovyScript",
            "x": 1100,
            "y": 280,
            "properties": {
                "groovyx-failure-strategy": "transfer to failure",
                "groovyx-script-body": NORMALIZE_SCRIPT,
            },
            "dynamicProperties": {
                "Lookup Directory": "#{lookupDir}",
                "DN Prefix": "ManagedElement=",
            },
            "autoTerminated": [],
        },
        {
            "name": "Build OMeS XML",
            "type": "ExecuteGroovyScript",
            "x": 1440,
            "y": 280,
            "properties": {
                "groovyx-failure-strategy": "transfer to failure",
                "groovyx-script-body": BUILD_XML_SCRIPT,
            },
            "dynamicProperties": {
                "File Format Version": "32.435 V10.0",
                "Vendor Name": "OMeS",
                "Granularity Period": "900",
            },
            "autoTerminated": [],
        },
        {
            "name": "UpdateAttribute",
            "type": "UpdateAttribute",
            "x": 1780,
            "y": 280,
            "properties": {
                "Store State": "Do not store state",
            },
            "dynamicProperties": {
                "mime.type": "application/xml",
                "filename": "${pm.generation.compact:isEmpty():ifElse(${now():format('yyyyMMddHHmmss')}, ${pm.generation.compact})}_${UUID()}.xml",
            },
            "autoTerminated": [],
        },
        {
            "name": "InvokeHTTP",
            "type": "InvokeHTTP",
            "x": 2120,
            "y": 280,
            "properties": {
                "HTTP Method": "POST",
                "Remote URL": "#{nbiEndpoint}",
                "send-message-body": "true",
                "Content-Type": "${mime.type}",
                "ssl-context-service": "@NbiSslContext",
                "Connection Timeout": "15 secs",
                "Read Timeout": "60 secs",
                "Socket Write Timeout": "60 secs",
                "idle-timeout": "5 mins",
                "Follow Redirects": "True",
                "Always Output Response": "false",
                "Penalize on \"No Retry\"": "true",
                "Add Response Headers to Request": "false",
                "Useragent": "nifi-cursor-automation/1.25.0",
            },
            "autoTerminated": ["Original", "Response"],
        },
        {
            "name": "LogAttribute",
            "type": "LogAttribute",
            "x": 1100,
            "y": 560,
            "properties": {
                "Log Level": "error",
                "Log Payload": "false",
                "Log prefix": "pm-omes-sftp-nbi/FAILURE",
                "Output Format": "Line per Attribute",
                "Attributes to Log": "filename,uuid,pm.parse.error,pm.normalize.error,pm.xml.error,invokehttp.status.code,invokehttp.status.message,invokehttp.java.exception.message",
            },
            "autoTerminated": ["success"],
        },
    ],
    "connections": [
        {
            "from": "GetSFTP",
            "to": "Parse PM Text To CSV",
            "relationships": ["success"],
            "name": "sftp-download",
        },
        {
            "from": "GetSFTP",
            "to": "LogAttribute",
            "relationships": ["comms.failure", "permission.denied"],
            "name": "sftp-errors",
        },
        {
            "from": "Parse PM Text To CSV",
            "to": "ConvertRecord",
            "relationships": ["success"],
            "name": "intermediate-csv",
        },
        {
            "from": "Parse PM Text To CSV",
            "to": "LogAttribute",
            "relationships": ["failure"],
            "name": "parse-failure",
        },
        {
            "from": "ConvertRecord",
            "to": "Normalize Map And Filter JSON",
            "relationships": ["success"],
            "name": "json-array",
        },
        {
            "from": "ConvertRecord",
            "to": "LogAttribute",
            "relationships": ["failure"],
            "name": "convert-failure",
        },
        {
            "from": "Normalize Map And Filter JSON",
            "to": "Build OMeS XML",
            "relationships": ["success"],
            "name": "normalized-json",
        },
        {
            "from": "Normalize Map And Filter JSON",
            "to": "LogAttribute",
            "relationships": ["failure"],
            "name": "normalize-failure",
        },
        {
            "from": "Build OMeS XML",
            "to": "UpdateAttribute",
            "relationships": ["success"],
            "name": "omes-xml",
        },
        {
            "from": "Build OMeS XML",
            "to": "LogAttribute",
            "relationships": ["failure"],
            "name": "xml-build-failure",
        },
        {
            "from": "UpdateAttribute",
            "to": "InvokeHTTP",
            "relationships": ["success"],
            "name": "ready-to-post",
        },
        {
            "from": "UpdateAttribute",
            "to": "LogAttribute",
            "relationships": ["failure"],
            "name": "attr-failure",
        },
        {
            "from": "InvokeHTTP",
            "to": "LogAttribute",
            "relationships": ["Failure", "Retry", "No Retry"],
            "name": "http-errors",
        },
    ],
}

out = Path(__file__).resolve().parent.parent / "flows" / "flow-pm-omes-sftp-nbi.json"
out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
print(f"Wrote {out}")
