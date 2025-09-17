package taxhelper.chatservice.domain.law;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.JsonNode;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

public class LawReferenceDeserializer extends JsonDeserializer<List<List<String>>> {
    @Override
    public List<List<String>> deserialize(JsonParser p, DeserializationContext ctxt)
            throws IOException {
        JsonNode node = p.getCodec().readTree(p);

        if (node == null || node.isNull() || !node.isArray() || node.size() == 0) {
            return new ArrayList<>();  // 절대 null 아님
        }

        List<List<String>> result = new ArrayList<>();
        for (JsonNode inner : node) {
            if (inner != null && inner.isArray()) {
                List<String> innerList = new ArrayList<>();
                for (JsonNode val : inner) {
                    if (val != null && !val.isNull()) {
                        innerList.add(val.asText());
                    }
                }
                result.add(innerList);
            }
        }
        return result;
    }
}